"""Agent trace of a conversation turn ("How this was made")."""

from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user, get_session
from app.models import User
from app.services.trace_service import TraceService

router = APIRouter(tags=["agent trace"])


@router.get("/projects/{project_id}/agent-jobs/{job_id}/trace")
async def get_trace(
    project_id: uuid.UUID,
    job_id: uuid.UUID,
    request: Request,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    """The graph path, step summaries and LLM calls of one turn. Owner only; LLM prompts for admins."""
    return await TraceService(session, request.app.state.redis).trace(
        job_id, user_id=user.id, project_id=project_id, include_debug=user.is_admin
    )
