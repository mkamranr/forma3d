"""Current scene + direct edits. Every edit returns 202 with a job id; the new version appears when
the Blender job commits (watch `GET /jobs/{id}/events`)."""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Path, Query, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user, get_session, get_settings_dep, rate_limit
from app.api.v1.versions import _version_out
from app.core.config import Settings
from app.models import User
from app.repositories.modeling import ModelingRepository
from app.schemas.modeling import HistoryStepOut, SubmitResponse
from app.schemas.project import ProjectOut
from app.schemas.scene import MaterialPatch, NewObject, ObjectMaterial, ObjectPatch, SceneOut
from app.services.job_service import job_out
from app.services.project_service import ProjectService
from app.services.scene_service import SceneService
from app.services.storage import ProjectStorage
from forma3d_shared.schemas.common import ID_PATTERN

router = APIRouter(prefix="/projects/{project_id}", tags=["scene"])

ObjectIdPath = Annotated[str, Path(pattern=ID_PATTERN, max_length=64)]
_jobs_limit = [Depends(rate_limit("jobs", "rate_limit_jobs_per_minute"))]


def _svc(request: Request, session: AsyncSession, settings: Settings) -> SceneService:
    return SceneService(session, request.app.state.redis, settings, ProjectStorage(settings.data_root))


def _accepted(result) -> SubmitResponse:
    job, batch = result
    return SubmitResponse(job_id=job.id, operation_batch_id=batch.id, status=job.status)


@router.get("/scene", response_model=SceneOut)
async def get_scene(
    project_id: uuid.UUID,
    request: Request,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings_dep),
) -> SceneOut:
    project, head, active = await _svc(request, session, settings).scene(user.id, project_id)
    version = None
    if head is not None:
        assets = await ModelingRepository(session).assets_for_versions([head.id])
        version = _version_out(project, head, assets.get(head.id, []))
    return SceneOut(
        project_id=str(project.id),
        version=version,
        inventory=head.object_inventory if head else {"objects": [], "materials": [], "collections": []},
        active_job=job_out(active) if active else None,
    )


@router.post(
    "/scene/starter", response_model=SubmitResponse, status_code=status.HTTP_202_ACCEPTED, dependencies=_jobs_limit
)
async def create_starter_scene(
    project_id: uuid.UUID,
    request: Request,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings_dep),
) -> SubmitResponse:
    return _accepted(await _svc(request, session, settings).starter_scene(user.id, project_id))


@router.post(
    "/scene/objects", response_model=SubmitResponse, status_code=status.HTTP_202_ACCEPTED, dependencies=_jobs_limit
)
async def add_object(
    project_id: uuid.UUID,
    body: NewObject,
    request: Request,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings_dep),
) -> SubmitResponse:
    return _accepted(await _svc(request, session, settings).add_object(user.id, project_id, body))


@router.patch(
    "/scene/objects/{object_id}",
    response_model=SubmitResponse,
    status_code=status.HTTP_202_ACCEPTED,
    dependencies=_jobs_limit,
)
async def edit_object(
    project_id: uuid.UUID,
    object_id: ObjectIdPath,
    body: ObjectPatch,
    request: Request,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings_dep),
) -> SubmitResponse:
    return _accepted(await _svc(request, session, settings).edit_object(user.id, project_id, object_id, body))


@router.delete(
    "/scene/objects/{object_id}",
    response_model=SubmitResponse,
    status_code=status.HTTP_202_ACCEPTED,
    dependencies=_jobs_limit,
)
async def delete_object(
    project_id: uuid.UUID,
    object_id: ObjectIdPath,
    request: Request,
    recursive: bool = Query(default=False),
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings_dep),
) -> SubmitResponse:
    return _accepted(await _svc(request, session, settings).delete_object(user.id, project_id, object_id, recursive))


@router.post(
    "/scene/objects/{object_id}/material",
    response_model=SubmitResponse,
    status_code=status.HTTP_202_ACCEPTED,
    dependencies=_jobs_limit,
)
async def give_object_material(
    project_id: uuid.UUID,
    object_id: ObjectIdPath,
    body: ObjectMaterial,
    request: Request,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings_dep),
) -> SubmitResponse:
    svc = _svc(request, session, settings)
    return _accepted(await svc.give_object_material(user.id, project_id, object_id, body))


@router.patch(
    "/scene/materials/{material_id}",
    response_model=SubmitResponse,
    status_code=status.HTTP_202_ACCEPTED,
    dependencies=_jobs_limit,
)
async def edit_material(
    project_id: uuid.UUID,
    material_id: ObjectIdPath,
    body: MaterialPatch,
    request: Request,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings_dep),
) -> SubmitResponse:
    return _accepted(await _svc(request, session, settings).edit_material(user.id, project_id, material_id, body))


@router.post("/versions/{version_id}/restore", response_model=ProjectOut)
async def restore_version(
    project_id: uuid.UUID,
    version_id: uuid.UUID,
    request: Request,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings_dep),
) -> ProjectOut:
    project = await _svc(request, session, settings).restore_version(user.id, project_id, version_id)
    return await ProjectService(session).get(user.id, project.id)


async def _step(direction: str, project_id, request, user, session, settings) -> HistoryStepOut:  # noqa: ANN001
    target = await _svc(request, session, settings).step_history(user.id, project_id, direction)
    project = await ProjectService(session).get(user.id, project_id)
    return HistoryStepOut(version_id=target.id, version_number=target.version_number, project=project)


@router.post("/modeling/undo", response_model=HistoryStepOut)
async def undo(
    project_id: uuid.UUID,
    request: Request,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings_dep),
) -> HistoryStepOut:
    """Move the head to its parent version (409 `nothing_to_undo`)."""
    return await _step("undo", project_id, request, user, session, settings)


@router.post("/modeling/redo", response_model=HistoryStepOut)
async def redo(
    project_id: uuid.UUID,
    request: Request,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings_dep),
) -> HistoryStepOut:
    """Move the head to its most recent child version (409 `nothing_to_redo`)."""
    return await _step("redo", project_id, request, user, session, settings)
