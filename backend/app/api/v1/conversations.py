"""Project conversation: read history, post a message (starts an agent job; follow it via SSE)."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Request, status
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user, get_session, get_settings_dep, rate_limit
from app.core.config import Settings
from app.models import Message, User
from app.services.conversation_service import ConversationService
from forma3d_shared.schemas.common import ID_PATTERN

router = APIRouter(prefix="/projects/{project_id}/conversation", tags=["conversation"])

PUBLIC_METADATA = (
    "version_id",
    "version_number",
    "action",
    "error_code",
    "agent_job_id",
    "selected_object_id",
    "selected_object_ids",
    "export_version_id",
    "export_version_number",
    "quality",
    "restored",
)


class MessageOut(BaseModel):
    id: uuid.UUID
    role: str
    content: str
    intent: str | None
    status: str
    metadata: dict[str, Any]
    created_at: datetime


class ConversationOut(BaseModel):
    id: uuid.UUID
    project_id: uuid.UUID
    messages: list[MessageOut]


class MessageIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    content: str = Field(min_length=1, max_length=4000)
    selected_object_id: str | None = Field(default=None, pattern=ID_PATTERN)
    #: Multi-selection (spec 13.3), primary object last.
    selected_object_ids: list[Annotated[str, Field(pattern=ID_PATTERN, max_length=64)]] = Field(
        default_factory=list, max_length=50
    )

    @field_validator("content")
    @classmethod
    def _clean(cls, v: str) -> str:
        v = v.replace("\x00", "").strip()
        if not v:
            raise ValueError("message is empty")
        return v


class PostMessageOut(BaseModel):
    message: MessageOut
    job_id: uuid.UUID
    status: str


def message_out(m: Message) -> MessageOut:
    return MessageOut(
        id=m.id,
        role=m.role,
        content=m.content,
        intent=m.intent,
        status=m.status,
        metadata={k: v for k, v in (m.metadata_ or {}).items() if k in PUBLIC_METADATA},
        created_at=m.created_at,
    )


@router.get("", response_model=ConversationOut)
async def get_conversation(
    project_id: uuid.UUID,
    request: Request,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings_dep),
) -> ConversationOut:
    svc = ConversationService(session, request.app.state.redis, settings)
    conv = await svc.get_or_create(user.id, project_id)
    return ConversationOut(
        id=conv.id, project_id=conv.project_id, messages=[message_out(m) for m in await svc.messages(conv)]
    )


@router.post(
    "/messages",
    response_model=PostMessageOut,
    status_code=status.HTTP_202_ACCEPTED,
    dependencies=[Depends(rate_limit("jobs", "rate_limit_jobs_per_minute"))],
)
async def post_message(
    project_id: uuid.UUID,
    body: MessageIn,
    request: Request,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings_dep),
) -> PostMessageOut:
    svc = ConversationService(session, request.app.state.redis, settings)
    message, job = await svc.post_message(
        user.id, project_id, body.content, body.selected_object_id, body.selected_object_ids
    )
    return PostMessageOut(message=message_out(message), job_id=job.id, status=job.status)
