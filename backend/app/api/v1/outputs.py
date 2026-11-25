"""Exports (spec §13.8), renders and texture uploads. Every route is ownership-checked; files are
served by id, never by path."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Literal

from fastapi import APIRouter, Depends, File, Request, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user, get_session, get_settings_dep, rate_limit
from app.core.config import Settings
from app.core.errors import ConflictError, NotFoundError
from app.models import Asset, Job, Project, User
from app.services.output_service import EXPORT_EXT, EXPORT_MEDIA, OutputService
from app.services.storage import ProjectStorage, StoragePathError
from forma3d_shared.schemas.job import RenderSettings

router = APIRouter(tags=["outputs"])
_limit = Depends(rate_limit("jobs", "rate_limit_jobs_per_minute"))

ExportFormat = Literal["glb", "gltf", "obj", "stl"]  # the allowlist


class ExportRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    format: ExportFormat
    version_id: uuid.UUID | None = None
    unit: Literal["m", "cm", "mm"] = "m"
    include_hidden: bool = False


class RenderRequest(RenderSettings):
    version_id: uuid.UUID | None = None


class OutputOut(BaseModel):
    id: uuid.UUID
    project_id: uuid.UUID
    status: str
    progress: dict[str, Any]
    error_message: str | None
    version_id: uuid.UUID | None
    version_number: int | None
    settings: dict[str, Any]
    details: dict[str, Any] = Field(default_factory=dict)
    size: int | None = None
    url: str | None = None  # download / image URL once ready
    created_at: datetime
    completed_at: datetime | None


class TextureOut(BaseModel):
    id: uuid.UUID
    name: str
    format: str
    width: int
    height: int
    size: int
    url: str
    created_at: datetime


def _svc(request: Request, session: AsyncSession, settings: Settings) -> OutputService:
    return OutputService(session, request.app.state.redis, settings, ProjectStorage(settings.data_root))


_SETTING_KEYS = {
    "export": ("format", "unit", "include_hidden"),
    "render": ("engine", "quality", "width", "height", "camera_object_id", "transparent_background"),
}


async def _out(svc: OutputService, job: Job) -> OutputOut:
    asset = await svc.asset(job) if job.status == "succeeded" else None
    p = job.payload
    path = "exports" if job.job_type == "export" else "renders"
    suffix = "download" if job.job_type == "export" else "image"
    return OutputOut(
        id=job.id,
        project_id=job.project_id,
        status=job.status,
        progress=job.progress,
        error_message=job.error_message,
        version_id=uuid.UUID(p["version_id"]) if p.get("version_id") else None,
        version_number=p.get("version_number"),
        settings={k: p.get(k) for k in _SETTING_KEYS[job.job_type]},
        details=p.get("details") or {},
        size=asset.file_size if asset else None,
        url=f"/api/v1/{path}/{job.id}/{suffix}" if asset else None,
        created_at=job.created_at,
        completed_at=job.completed_at,
    )


def _file(settings: Settings, asset: Asset, media_type: str, filename: str) -> FileResponse:
    try:
        path = ProjectStorage(settings.data_root).resolve(asset.file_path)
    except StoragePathError as exc:
        raise NotFoundError("File not found") from exc
    if not path.is_file():
        raise NotFoundError("File is missing")
    return FileResponse(
        path,
        media_type=media_type,
        filename=filename,
        headers={"Cache-Control": "private, max-age=31536000, immutable", "X-Content-Type-Options": "nosniff"},
    )


def _slug(project: Project) -> str:
    keep = "".join(c if c.isalnum() else "-" for c in project.name.lower()).strip("-")
    return "-".join(filter(None, keep.split("-")))[:40] or "model"


# --------------------------------------------------------------------------- exports
@router.post("/projects/{project_id}/exports", response_model=OutputOut, status_code=202, dependencies=[_limit])
async def create_export(
    project_id: uuid.UUID,
    body: ExportRequest,
    request: Request,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings_dep),
) -> OutputOut:
    svc = _svc(request, session, settings)
    job = await svc.create_export(
        user.id, project_id, body.format, version_id=body.version_id, unit=body.unit, include_hidden=body.include_hidden
    )
    return await _out(svc, job)


@router.get("/projects/{project_id}/exports", response_model=list[OutputOut])
async def list_exports(
    project_id: uuid.UUID,
    request: Request,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings_dep),
) -> list[OutputOut]:
    svc = _svc(request, session, settings)
    project = await svc.project(user.id, project_id)
    return [await _out(svc, j) for j in await svc.list_jobs(project, "export")]


@router.get("/exports/{export_id}", response_model=OutputOut)
async def get_export(
    export_id: uuid.UUID,
    request: Request,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings_dep),
) -> OutputOut:
    svc = _svc(request, session, settings)
    return await _out(svc, await svc.owned_job(user.id, export_id, "export"))


@router.get("/exports/{export_id}/download")
async def download_export(
    export_id: uuid.UUID,
    request: Request,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings_dep),
) -> FileResponse:
    svc = _svc(request, session, settings)
    job = await svc.owned_job(user.id, export_id, "export")
    asset = await svc.asset(job) if job.status == "succeeded" else None
    if asset is None:
        raise ConflictError("The export is not ready", code="not_ready")
    project = await svc.project(user.id, job.project_id)
    fmt = job.payload.get("format", "glb")
    unit = job.payload.get("unit", "m")
    suffix = "" if unit == "m" or fmt in ("glb", "gltf") else f"-{unit}"
    name = f"{_slug(project)}-v{job.payload.get('version_number', 0)}{suffix}"
    return _file(settings, asset, EXPORT_MEDIA[fmt], f"{name}.{EXPORT_EXT[fmt]}")


# --------------------------------------------------------------------------- renders
@router.post("/projects/{project_id}/renders", response_model=OutputOut, status_code=202, dependencies=[_limit])
async def create_render(
    project_id: uuid.UUID,
    body: RenderRequest,
    request: Request,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings_dep),
) -> OutputOut:
    svc = _svc(request, session, settings)
    render = RenderSettings(**body.model_dump(exclude={"version_id"}))
    job = await svc.create_render(user.id, project_id, render, body.version_id)
    return await _out(svc, job)


@router.get("/projects/{project_id}/renders", response_model=list[OutputOut])
async def list_renders(
    project_id: uuid.UUID,
    request: Request,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings_dep),
) -> list[OutputOut]:
    svc = _svc(request, session, settings)
    project = await svc.project(user.id, project_id)
    return [await _out(svc, j) for j in await svc.list_jobs(project, "render")]


@router.get("/renders/{render_id}", response_model=OutputOut)
async def get_render(
    render_id: uuid.UUID,
    request: Request,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings_dep),
) -> OutputOut:
    svc = _svc(request, session, settings)
    return await _out(svc, await svc.owned_job(user.id, render_id, "render"))


@router.get("/renders/{render_id}/image")
async def render_image(
    render_id: uuid.UUID,
    request: Request,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings_dep),
) -> FileResponse:
    svc = _svc(request, session, settings)
    job = await svc.owned_job(user.id, render_id, "render")
    asset = await svc.asset(job) if job.status == "succeeded" else None
    if asset is None:
        raise ConflictError("The render is not ready", code="not_ready")
    project = await svc.project(user.id, job.project_id)
    return _file(settings, asset, "image/png", f"{_slug(project)}-v{job.payload.get('version_number', 0)}-render.png")


# --------------------------------------------------------------------------- textures
def _texture_out(project_id: uuid.UUID, a: Asset) -> TextureOut:
    m = a.metadata_ or {}
    return TextureOut(
        id=a.id,
        name=m.get("name", "texture"),
        format=m.get("format", "png"),
        width=int(m.get("width", 0)),
        height=int(m.get("height", 0)),
        size=a.file_size,
        url=f"/api/v1/projects/{project_id}/textures/{a.id}",
        created_at=a.created_at,
    )


@router.post("/projects/{project_id}/textures", response_model=TextureOut, status_code=201, dependencies=[_limit])
async def upload_texture(
    project_id: uuid.UUID,
    request: Request,
    file: UploadFile = File(...),
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings_dep),
) -> TextureOut:
    svc = _svc(request, session, settings)
    data = await file.read(svc.limits.max_texture_upload_bytes + 1)
    asset = await svc.upload_texture(user.id, project_id, file.filename or "texture", data)
    return _texture_out(project_id, asset)


@router.get("/projects/{project_id}/textures", response_model=list[TextureOut])
async def list_textures(
    project_id: uuid.UUID,
    request: Request,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings_dep),
) -> list[TextureOut]:
    svc = _svc(request, session, settings)
    project = await svc.project(user.id, project_id)
    return [_texture_out(project.id, a) for a in await svc.textures(project)]


@router.get("/projects/{project_id}/textures/{texture_id}")
async def texture_image(
    project_id: uuid.UUID,
    texture_id: uuid.UUID,
    request: Request,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings_dep),
) -> FileResponse:
    svc = _svc(request, session, settings)
    project = await svc.project(user.id, project_id)
    asset = await session.get(Asset, texture_id)
    if asset is None or asset.project_id != project.id or asset.asset_type != "texture":
        raise NotFoundError("Texture not found")
    fmt = (asset.metadata_ or {}).get("format", "png")
    return _file(
        settings, asset, "image/png" if fmt == "png" else "image/jpeg", f"texture.{'png' if fmt == 'png' else 'jpg'}"
    )
