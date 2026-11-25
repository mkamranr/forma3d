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
from app.core.errors import AppError, AuthError, ConflictError, NotFoundError
from app.models import SceneVersion
from app.repositories.modeling import ModelingRepository
from app.schemas.modeling import JobOut
from app.services.conversation_service import ConversationService
from app.services.job_service import JobService, job_out
from app.services.storage import ProjectStorage
from forma3d_shared.schemas.agent import (
    AgentCompleteRequest,
    AgentContext,
    AgentRunRecord,
    RestoreRequest,
    RestoreResponse,
    StagedCommitResponse,
    SubmitPlanRequest,
    SubmitPlanResponse,
)
from forma3d_shared.schemas.job import BlenderJobResult, JobStartResponse
from forma3d_shared.schemas.scene_object import SceneInventory


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
    #: The worker reclaimed this message after its previous holder died (XAUTOCLAIM): an agent
    #: job that is still `running` may be resumed from its checkpoint.
    reclaim: bool = False


class DiscardRequest(BaseModel):
    reason: str = Field(default="Discarded after review", max_length=500)


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
    return await _service(request, session, settings).start(job_id, body.worker_id, reclaim=body.reclaim)


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


@router.get("/jobs/{job_id}/staged", response_model=SceneInventory)
async def staged_inventory(
    job_id: uuid.UUID,
    request: Request,
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings_dep),
) -> SceneInventory:
    job = await _service(request, session, settings).staged_job(job_id)
    result = job.payload.get("staged_result") if job.status == "staged" else None
    if not result or result.get("inventory") is None:
        raise ConflictError(f"Job is {job.status}", code="job_not_staged")
    return SceneInventory.model_validate(result["inventory"])


@router.post("/jobs/{job_id}/commit", response_model=StagedCommitResponse)
async def commit_staged(
    job_id: uuid.UUID,
    request: Request,
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings_dep),
) -> StagedCommitResponse:
    job = await _service(request, session, settings).commit_staged(job_id)
    version = await session.get(SceneVersion, uuid.UUID(job.payload["result_version_id"]))
    assert version is not None
    return StagedCommitResponse(version_id=version.id, version_number=version.version_number)


@router.post("/jobs/{job_id}/discard", response_model=JobOut)
async def discard_staged(
    job_id: uuid.UUID,
    body: DiscardRequest,
    request: Request,
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings_dep),
) -> JobOut:
    return job_out(await _service(request, session, settings).discard_staged(job_id, body.reason))


# ---------------------------------------------------------------------------------- agent jobs
@router.post("/agent-jobs/{job_id}/restore", response_model=RestoreResponse)
async def agent_restore(
    job_id: uuid.UUID,
    body: RestoreRequest,
    request: Request,
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings_dep),
) -> RestoreResponse:
    version = await _conv(request, session, settings).restore(job_id, body)
    return RestoreResponse(version_id=version.id, version_number=version.version_number)


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
