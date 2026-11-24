"""Raw DSL batch submission (dev/integration path; UI edits and agents reuse the same service)."""

from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, Body, Depends, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user, get_session, get_settings_dep, rate_limit
from app.core.config import Settings
from app.models import User
from app.schemas.modeling import SubmitResponse
from app.services.modeling_service import ModelingService
from app.services.storage import ProjectStorage

router = APIRouter(prefix="/projects/{project_id}/modeling", tags=["modeling"])


@router.post(
    "/execute",
    response_model=SubmitResponse,
    status_code=status.HTTP_202_ACCEPTED,
    dependencies=[Depends(rate_limit("jobs", "rate_limit_jobs_per_minute"))],
)
async def execute_batch(
    project_id: uuid.UUID,
    request: Request,
    body: dict[str, Any] = Body(...),
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings_dep),
) -> SubmitResponse:
    service = ModelingService(session, request.app.state.redis, settings, ProjectStorage(settings.data_root))
    job, batch = await service.submit_batch(user.id, project_id, body)
    return SubmitResponse(job_id=job.id, operation_batch_id=batch.id, status=job.status)
