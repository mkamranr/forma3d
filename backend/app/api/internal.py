"""Internal worker callbacks. Only reachable on the internal `app` network (nginx never proxies
`/internal`), and additionally protected by a shared service token."""

from __future__ import annotations

import secrets
import uuid

from fastapi import APIRouter, Depends, Header, Request, Response
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_session, get_settings_dep
from app.core.config import Settings
from app.core.errors import AppError, AuthError, NotFoundError
from app.repositories.modeling import ModelingRepository
from app.schemas.modeling import JobOut
from app.services.conversation_service import ConversationService
from app.services.job_service import JobService, job_out
from app.services.storage import ProjectStorage
from forma3d_shared.schemas.agent import (
    AgentCompleteRequest,
    AgentContext,
    AgentRunRecord,
    SubmitPlanRequest,
    SubmitPlanResponse,
)
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


# ---------------------------------------------------------------------------------- jobs (read)
@router.get("/jobs/{job_id}", response_model=JobOut)
async def get_job_internal(
    job_id: uuid.UUID,
    request: Request,
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings_dep),
) -> JobOut:
    job = await ModelingRepository(session).get_job(job_id)
    if job is None:
        raise NotFoundError("Job not found")
    return job_out(job)


# ---------------------------------------------------------------------------------- agent jobs
def _conv(request: Request, session: AsyncSession, settings: Settings) -> ConversationService:
    return ConversationService(session, request.app.state.redis, settings)


@router.get("/agent-jobs/{job_id}/context", response_model=AgentContext)
async def agent_context(
    job_id: uuid.UUID,
    request: Request,
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings_dep),
) -> AgentContext:
    return await _conv(request, session, settings).context(job_id)


@router.post("/agent-jobs/{job_id}/plan", response_model=SubmitPlanResponse)
async def agent_submit_plan(
    job_id: uuid.UUID,
    body: SubmitPlanRequest,
    request: Request,
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings_dep),
) -> SubmitPlanResponse:
    mjob, batch = await _conv(request, session, settings).submit_plan(
        job_id, body, ProjectStorage(settings.data_root)
    )
    return SubmitPlanResponse(modeling_job_id=mjob.id, operation_batch_id=batch.id)


@router.post("/agent-jobs/{job_id}/runs", status_code=204)
async def agent_record_run(
    job_id: uuid.UUID,
    body: AgentRunRecord,
    request: Request,
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings_dep),
) -> Response:
    await _conv(request, session, settings).record_run(job_id, body)
    return Response(status_code=204)


@router.post("/agent-jobs/{job_id}/complete", status_code=204)
async def agent_complete(
    job_id: uuid.UUID,
    body: AgentCompleteRequest,
    request: Request,
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings_dep),
) -> Response:
    await _conv(request, session, settings).complete(job_id, body)
    return Response(status_code=204)
