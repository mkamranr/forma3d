"""Version history and ownership-checked asset downloads. Clients never see storage paths."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends
from fastapi.responses import FileResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user, get_session, get_settings_dep
from app.core.config import Settings
from app.core.errors import NotFoundError
from app.models import Asset, Project, SceneVersion, User
from app.repositories.modeling import ModelingRepository
from app.repositories.projects import ProjectRepository
from app.schemas.modeling import AssetKind, AssetOut, VersionDetail, VersionOut
from app.services.storage import ProjectStorage, StoragePathError

router = APIRouter(prefix="/projects/{project_id}", tags=["versions"])

MEDIA_TYPES = {
    "glb": "model/gltf-binary",
    "thumbnail": "image/png",
    "blend": "application/x-blender",
    "inventory": "application/json",
    "operations": "application/json",
}


async def _project(session: AsyncSession, user: User, project_id: uuid.UUID) -> Project:
    project = await ProjectRepository(session).get_owned(user.id, project_id)
    if project is None:
        raise NotFoundError("Project not found")
    return project


def _asset_out(project_id: uuid.UUID, version_id: uuid.UUID, a: Asset) -> AssetOut:
    return AssetOut(
        kind=a.asset_type,
        size=a.file_size,
        sha256=a.checksum,
        url=f"/api/v1/projects/{project_id}/versions/{version_id}/assets/{a.asset_type}",
    )


def _version_out(project: Project, v: SceneVersion, assets: list[Asset], cls=VersionOut, **extra) -> VersionOut:
    return cls(
        id=v.id,
        project_id=v.project_id,
        parent_version_id=v.parent_version_id,
        version_number=v.version_number,
        summary=v.summary or "",
        status=v.status,
        operation_batch_id=v.operation_batch_id,
        message_id=v.message_id,
        is_current=project.current_version_id == v.id,
        object_count=len((v.object_inventory or {}).get("objects", [])),
        assets=[_asset_out(project.id, v.id, a) for a in sorted(assets, key=lambda a: a.asset_type)],
        created_at=v.created_at,
        **extra,
    )


@router.get("/versions", response_model=list[VersionOut])
async def list_versions(
    project_id: uuid.UUID,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> list[VersionOut]:
    project = await _project(session, user, project_id)
    repo = ModelingRepository(session)
    versions = await repo.list_versions(project.id)
    assets = await repo.assets_for_versions([v.id for v in versions])
    return [_version_out(project, v, assets.get(v.id, [])) for v in versions]


@router.get("/versions/{version_id}", response_model=VersionDetail)
async def get_version(
    project_id: uuid.UUID,
    version_id: uuid.UUID,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> VersionOut:
    project = await _project(session, user, project_id)
    repo = ModelingRepository(session)
    version = await repo.get_version(project.id, version_id)
    if version is None:
        raise NotFoundError("Version not found")
    assets = await repo.assets_for_versions([version.id])
    return _version_out(
        project, version, assets.get(version.id, []), VersionDetail, inventory=version.object_inventory or {}
    )


def _file_response(settings: Settings, rel_path: str, kind: str, filename: str) -> FileResponse:
    try:
        path = ProjectStorage(settings.data_root).resolve(rel_path)
    except StoragePathError as exc:
        raise NotFoundError("Asset not found") from exc
    if not path.is_file():
        raise NotFoundError("Asset file is missing")
    return FileResponse(
        path,
        media_type=MEDIA_TYPES.get(kind, "application/octet-stream"),
        filename=filename,
        headers={"Cache-Control": "private, max-age=31536000, immutable", "X-Content-Type-Options": "nosniff"},
    )


@router.get("/versions/{version_id}/assets/{kind}")
async def get_asset(
    project_id: uuid.UUID,
    version_id: uuid.UUID,
    kind: AssetKind,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings_dep),
) -> FileResponse:
    project = await _project(session, user, project_id)
    repo = ModelingRepository(session)
    version = await repo.get_version(project.id, version_id)
    asset = await repo.get_asset(version.id, kind) if version else None
    if version is None or asset is None:
        raise NotFoundError("Asset not found")
    ext = asset.file_path.rsplit(".", 1)[-1]
    return _file_response(settings, asset.file_path, kind, f"v{version.version_number:04d}-{kind}.{ext}")


@router.get("/thumbnail")
async def project_thumbnail(
    project_id: uuid.UUID,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings_dep),
) -> FileResponse:
    project = await _project(session, user, project_id)
    if not project.thumbnail_path:
        raise NotFoundError("Project has no thumbnail yet")
    response = _file_response(settings, project.thumbnail_path, "thumbnail", "thumbnail.png")
    response.headers["Cache-Control"] = "private, no-cache"  # head changes over time
    return response
