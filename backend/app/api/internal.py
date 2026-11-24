"""Internal worker callbacks. Only reachable on the internal `app` network (nginx never proxies
`/internal`), and additionally protected by a shared service token."""

from __future__ import annotations

import secrets
import uuid

from fastapi import APIRouter, Depends, Header, Request
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_session, get_settings_dep
from app.core.config import Settings
from app.core.errors import AppError, AuthError
from app.schemas.modeling import JobOut
from app.services.job_service import JobService
from app.services.storage import ProjectStorage
from forma3d_shared.schemas.job import BlenderJobResult, JobStartResponse


async def require_service_token(
    x_internal_token: str | None = Header(default=None),
    settings: Settings = Depends(get_settings_dep),
) -> None:
    if not x_internal_token or not secrets.compare_digest(
        x_internal_token.encode(), settings.internal_service_token.encode()
    ):
        raise AuthError("Invalid service token", code="invalid_service_token")


router = APIRouter(
    prefix="/internal/v1", tags=["internal"], dependencies=[Depends(require_service_token)], include_in_schema=False
)


class StartRequest(BaseModel):
    worker_id: str = Field(min_length=1, max_length=64)


def _service(request: Request, session: AsyncSession, settings: Settings) -> JobService:
    return JobService(session, request.app.state.redis, ProjectStorage(settings.data_root))


@router.post("/jobs/{job_id}/start", response_model=JobStartResponse)
async def start_job(
    job_id: uuid.UUID,
    body: StartRequest,
    request: Request,
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings_dep),
) -> JobStartResponse:
    return await _service(request, session, settings).start(job_id, body.worker_id)


@router.post("/jobs/{job_id}/complete", response_model=JobOut)
async def complete_job(
    job_id: uuid.UUID,
    body: BlenderJobResult,
    request: Request,
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings_dep),
) -> JobOut:
    if body.job_id != job_id:
        raise AppError("job_id mismatch", code="job_id_mismatch", status_code=422)
    return await _service(request, session, settings).complete(body)
