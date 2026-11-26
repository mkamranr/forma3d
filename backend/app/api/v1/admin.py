"""Configuration page API: app settings (admin), user management (admin), client defaults (public)."""

from __future__ import annotations

import asyncio
import json
import uuid
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_session, get_settings_dep, require_admin
from app.core.config import Settings
from app.core.errors import AppError, ConflictError, NotFoundError, ServiceUnavailableError
from app.core.logging import get_logger
from app.core.time import utcnow
from app.models import Project, RefreshToken, User
from forma3d_shared.runtime_config import LLM_TEST_QUEUE, LLM_TEST_RESULT

log = get_logger(__name__)
router = APIRouter(tags=["configuration"])
admin = APIRouter(prefix="/admin", tags=["configuration"], dependencies=[Depends(require_admin)])

LLM_TEST_TIMEOUT = 90.0


def _runtime(request: Request):  # noqa: ANN202
    return request.app.state.runtime


# --------------------------------------------------------------------------- public
@router.get("/config")
async def client_config(
    request: Request,
    _settings: Settings = Depends(get_settings_dep),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    """Non-sensitive settings the browser needs (sign-up allowed, dialog defaults). No login required."""
    cfg = _runtime(request).client()
    if not cfg["registration_open"]:  # the first account (the administrator) can always be created
        cfg["registration_open"] = (await session.execute(select(func.count(User.id)))).scalar_one() == 0
    return cfg


# --------------------------------------------------------------------------- settings
class ConfigUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    #: key -> new value; null resets to the .env / built-in default.
    values: dict[str, Any] = Field(max_length=100)


@admin.get("/config")
async def get_config(request: Request, _settings: Settings = Depends(get_settings_dep)) -> dict[str, Any]:
    return _runtime(request).describe()


@admin.put("/config")
async def put_config(
    body: ConfigUpdate,
    request: Request,
    user: User = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    runtime = _runtime(request)
    await runtime.save(session, request.app.state.redis, request.app.state.db.sessionmaker, body.values, user.id)
    return runtime.describe()


class LlmTest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    #: Unsaved form values to try (same keys as the AI model settings); saved values fill the rest.
    values: dict[str, Any] = Field(default_factory=dict, max_length=50)


@admin.post("/config/test-llm")
async def test_llm(body: LlmTest, request: Request) -> dict[str, Any]:
    """Runs on an agent worker (only workers can reach the model): lists models, sends a one-line prompt and,
    with a vision model, a tiny test image. Returns each check with timing."""
    redis = request.app.state.redis
    if redis is None:
        raise ServiceUnavailableError("Redis is not available")
    keys = [k.decode() if isinstance(k, bytes) else str(k) async for k in redis.scan_iter(match="workers:agent:*")]
    alive = [k for k in keys if not k.endswith(":llm")]
    if not alive:
        raise ServiceUnavailableError(
            "No assistant worker is running, so the connection cannot be tested", code="no_agent_worker"
        )
    test_id = uuid.uuid4().hex
    values = _runtime(request).agent_values(body.values)
    await redis.rpush(LLM_TEST_QUEUE, json.dumps({"id": test_id, "values": values}))
    await redis.expire(LLM_TEST_QUEUE, 300)
    key = LLM_TEST_RESULT.format(id=test_id)
    deadline = asyncio.get_running_loop().time() + LLM_TEST_TIMEOUT
    while asyncio.get_running_loop().time() < deadline:
        raw = await redis.get(key)
        if raw:
            await redis.delete(key)
            return json.loads(raw)
        await asyncio.sleep(0.5)
    raise ServiceUnavailableError("The assistant worker did not answer in time", code="llm_test_timeout")


# --------------------------------------------------------------------------- users
class AdminUser(BaseModel):
    id: uuid.UUID
    email: str
    display_name: str
    is_active: bool
    is_admin: bool
    created_at: datetime
    projects: int


class AdminUserList(BaseModel):
    items: list[AdminUser]
    total: int


class UserUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    is_active: bool | None = None
    is_admin: bool | None = None


@admin.get("/users", response_model=AdminUserList)
async def list_users(
    q: str = Query(default="", max_length=200),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    session: AsyncSession = Depends(get_session),
) -> AdminUserList:
    where = []
    if q.strip():
        like = f"%{q.strip().lower()}%"
        where.append(or_(func.lower(User.email).like(like), func.lower(User.display_name).like(like)))
    total = (await session.execute(select(func.count(User.id)).where(*where))).scalar_one()
    counts = (
        select(Project.user_id, func.count(Project.id).label("n"))
        .where(Project.status != "deleted")
        .group_by(Project.user_id)
        .subquery()
    )
    rows = await session.execute(
        select(User, func.coalesce(counts.c.n, 0))
        .outerjoin(counts, counts.c.user_id == User.id)
        .where(*where)
        .order_by(User.created_at)
        .limit(limit)
        .offset(offset)
    )
    items = [
        AdminUser(
            id=u.id,
            email=u.email,
            display_name=u.display_name,
            is_active=u.is_active,
            is_admin=u.is_admin,
            created_at=u.created_at,
            projects=int(n),
        )
        for u, n in rows.all()
    ]
    return AdminUserList(items=items, total=int(total))


@admin.patch("/users/{user_id}", response_model=AdminUser)
async def update_user(
    user_id: uuid.UUID,
    body: UserUpdate,
    me: User = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
) -> AdminUser:
    user = await session.get(User, user_id)
    if user is None:
        raise NotFoundError("User not found")
    if user.id == me.id and (body.is_active is False or body.is_admin is False):
        raise ConflictError("You cannot deactivate yourself or remove your own admin rights", code="self_change")
    if (body.is_admin is False or body.is_active is False) and user.is_admin:
        admins = (
            await session.execute(select(func.count(User.id)).where(User.is_admin.is_(True), User.is_active.is_(True)))
        ).scalar_one()
        if admins <= 1:
            raise ConflictError("At least one active administrator is required", code="last_admin")
    if body.is_admin is not None:
        user.is_admin = body.is_admin
    if body.is_active is not None and body.is_active != user.is_active:
        user.is_active = body.is_active
        if not body.is_active:  # sign them out everywhere (access tokens are re-checked on every request)
            await session.execute(
                update(RefreshToken)
                .where(RefreshToken.user_id == user.id, RefreshToken.revoked_at.is_(None))
                .values(revoked_at=utcnow())
            )
    if body.is_active is None and body.is_admin is None:
        raise AppError("Nothing to change", code="invalid_request", status_code=422)
    await session.commit()
    log.info("admin.user_updated", user_id=str(user.id), by=str(me.id), **body.model_dump(exclude_none=True))
    projects = (
        await session.execute(
            select(func.count(Project.id)).where(Project.user_id == user.id, Project.status != "deleted")
        )
    ).scalar_one()
    return AdminUser(
        id=user.id,
        email=user.email,
        display_name=user.display_name,
        is_active=user.is_active,
        is_admin=user.is_admin,
        created_at=user.created_at,
        projects=int(projects),
    )


router.include_router(admin)
