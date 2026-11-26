"""Renders, exports and texture uploads (Phase 7).

Renders and exports are *derived* Blender jobs: they read an immutable version, never change the
scene, and so neither take the project lock nor wait for the assistant. Their outputs are stored as
assets of the version they were made from and cached — asking twice for the same output returns
the first one. Texture images are uploaded per project and referenced from the DSL by asset id
only (`assign_texture.image_id`); the Blender worker receives verified copies, never a user path.
"""

from __future__ import annotations

import asyncio
import hashlib
import os
import shutil
import struct
import uuid
from pathlib import Path
from typing import Any

from redis.asyncio import Redis
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.errors import AppError, ConflictError, NotFoundError, ServiceUnavailableError
from app.core.logging import get_logger
from app.core.runtime_config import current_limits
from app.core.time import utcnow
from app.models import Asset, Job, Project, SceneVersion
from app.repositories.modeling import ModelingRepository
from app.repositories.projects import ProjectRepository
from app.services.events import EventPublisher
from app.services.storage import ProjectStorage
from app.services.version_service import sha256_file
from forma3d_shared.schemas.job import (
    BLENDER_JOB_STREAM,
    BlenderJobResult,
    BlenderJobSpec,
    ExportSettings,
    RenderSettings,
    SourceVersion,
    TextureFile,
)

log = get_logger(__name__)

OUTPUT_JOB_TYPES = ("render", "export")
#: Uploaded image kinds and their folder in the project directory.
IMAGE_ASSET_DIRS = {"texture": "textures", "reference": "references"}
EXPORT_MEDIA = {"glb": "model/gltf-binary", "gltf": "application/zip", "obj": "application/zip", "stl": "model/stl"}
EXPORT_EXT = {"glb": "glb", "gltf": "gltf.zip", "obj": "obj.zip", "stl": "stl"}
_EMPTY_DIGEST = hashlib.sha256(b'{"operations":[],"schema_version":"1.0"}').hexdigest()


# --------------------------------------------------------------------------- image checks
class ImageError(ValueError):
    pass


def image_info(data: bytes) -> tuple[str, int, int]:
    """(format, width, height) from the file header — PNG or baseline/progressive JPEG only."""
    if data[:8] == b"\x89PNG\r\n\x1a\n" and len(data) >= 24 and data[12:16] == b"IHDR":
        w, h = struct.unpack(">II", data[16:24])
        return "png", w, h
    if data[:3] == b"\xff\xd8\xff":
        i = 2
        while i + 9 < len(data):
            if data[i] != 0xFF:
                raise ImageError("corrupt JPEG")
            marker = data[i + 1]
            if marker in (0xD8, 0x01) or 0xD0 <= marker <= 0xD7:
                i += 2
                continue
            seg = struct.unpack(">H", data[i + 2 : i + 4])[0]
            if 0xC0 <= marker <= 0xCF and marker not in (0xC4, 0xC8, 0xCC):
                h, w = struct.unpack(">HH", data[i + 5 : i + 9])
                return "jpeg", w, h
            i += 2 + seg
        raise ImageError("JPEG has no frame header")
    raise ImageError("only PNG and JPEG images are supported")


class OutputService:
    def __init__(self, session: AsyncSession, redis: Redis, settings: Settings, storage: ProjectStorage) -> None:
        self.session = session
        self.redis = redis
        self.settings = settings
        self.storage = storage
        self.repo = ModelingRepository(session)
        self.events = EventPublisher(redis)
        self.limits = current_limits()

    # ------------------------------------------------------------------ lookups
    async def project(self, user_id: uuid.UUID, project_id: uuid.UUID) -> Project:
        project = await ProjectRepository(self.session).get_owned(user_id, project_id)
        if project is None:
            raise NotFoundError("Project not found")
        return project

    async def _version(self, project: Project, version_id: uuid.UUID | None) -> SceneVersion:
        vid = version_id or project.current_version_id
        version = await self.repo.get_version(project.id, vid) if vid else None
        if version is None:
            raise NotFoundError("Version not found" if version_id else "The project has no model yet")
        return version

    async def owned_job(self, user_id: uuid.UUID, job_id: uuid.UUID, job_type: str) -> Job:
        job = await self.repo.get_owned_job(user_id, job_id)
        if job is None or job.job_type != job_type:
            raise NotFoundError(f"{job_type.title()} not found")
        return job

    async def list_jobs(self, project: Project, job_type: str, limit: int = 30) -> list[Job]:
        stmt = (
            select(Job)
            .where(Job.project_id == project.id, Job.job_type == job_type)
            .order_by(Job.created_at.desc())
            .limit(limit)
        )
        return list((await self.session.execute(stmt)).scalars())

    async def asset(self, job: Job) -> Asset | None:
        aid = job.payload.get("asset_id")
        return await self.session.get(Asset, uuid.UUID(aid)) if aid else None

    # ------------------------------------------------------------------ create
    async def _cached(self, version: SceneVersion, asset_type: str, key: dict[str, Any]) -> Asset | None:
        rows = await self.session.execute(
            select(Asset).where(Asset.version_id == version.id, Asset.asset_type == asset_type)
        )
        for a in rows.scalars():
            if (a.metadata_ or {}).get("key") == key and self.storage.resolve(a.file_path).is_file():
                return a
        return None

    async def _pending_for_user(self, user_id: uuid.UUID) -> int:
        stmt = (
            select(func.count(Job.id))
            .join(Project, Project.id == Job.project_id)
            .where(
                Project.user_id == user_id,
                Job.job_type.in_(OUTPUT_JOB_TYPES),
                Job.status.in_(("queued", "running")),
            )
        )
        return int((await self.session.execute(stmt)).scalar_one())

    async def _done_job(
        self, project: Project, version: SceneVersion, job_type: str, payload: dict, asset: Asset
    ) -> Job:
        now = utcnow()
        job = Job(
            id=uuid.uuid4(),
            project_id=project.id,
            job_type=job_type,
            status="succeeded",
            progress={"stage": "done", "message": "Ready"},
            payload={
                **payload,
                "asset_id": str(asset.id),
                "cached": True,
                "details": asset.metadata_.get("details", {}),
            },
            started_at=now,
            completed_at=now,
        )
        self.session.add(job)
        await self.session.commit()
        return job

    async def _enqueue(
        self, project: Project, version: SceneVersion, job_type: str, payload: dict, **settings: Any
    ) -> Job:
        if await self._pending_for_user(project.user_id) >= self.settings.max_pending_outputs:
            raise ConflictError("Too many renders/exports are still running; wait for them to finish", code="busy")
        blend = await self.repo.get_asset(version.id, "blend")
        if blend is None:
            raise AppError("This version has no .blend file", code="corrupt_version", status_code=500)
        job_id = uuid.uuid4()
        staging_dir = self.storage.staging_dir(project.user_id, project.id, job_id)
        job = Job(
            id=job_id,
            project_id=project.id,
            job_type=job_type,
            status="queued",
            progress={"stage": "queued", "message": "Waiting for Blender worker"},
            payload={**payload, "staging_dir": staging_dir, "target_version_number": version.version_number},
        )
        self.session.add(job)
        await self.session.commit()
        spec = BlenderJobSpec(
            job_id=job_id,
            kind=job_type,  # type: ignore[arg-type]
            project_id=project.id,
            user_id=project.user_id,
            target_version_number=version.version_number,
            source=SourceVersion(
                version_id=version.id,
                version_number=version.version_number,
                blend_path=blend.file_path,
                blend_sha256=blend.checksum,
            ),
            staging_dir=staging_dir,
            operations=[],
            batch_sha256=_EMPTY_DIGEST,
            render_thumbnail=False,
            timeout_seconds=self.settings.render_timeout_seconds
            if job_type == "render"
            else self.settings.job_timeout_seconds,
            **settings,
        )
        try:
            await self.redis.xadd(BLENDER_JOB_STREAM, {"job": spec.model_dump_json()})
        except Exception as exc:
            job.status, job.error_message = "failed", "Job queue unavailable"
            await self.session.commit()
            raise ServiceUnavailableError("The Blender queue is unavailable, please try again") from exc
        await self.events.publish(job.id, "job.queued", stage="queued", message="Waiting for Blender worker")
        log.info("output.queued", job_id=str(job.id), kind=job_type, version=version.version_number)
        return job

    async def create_export(
        self,
        user_id: uuid.UUID,
        project_id: uuid.UUID,
        fmt: str,
        *,
        version_id: uuid.UUID | None = None,
        unit: str = "m",
        include_hidden: bool = False,
    ) -> Job:
        project = await self.project(user_id, project_id)
        version = await self._version(project, version_id)
        payload = {
            "format": fmt,
            "unit": unit,
            "include_hidden": include_hidden,
            "version_id": str(version.id),
            "version_number": version.version_number,
        }
        if fmt == "glb":  # the version's own artifact: nothing to build
            glb = await self.repo.get_asset(version.id, "glb")
            if glb is None:
                raise NotFoundError("This version has no GLB")
            return await self._done_job(project, version, "export", payload, glb)
        settings = ExportSettings(format=fmt, unit=unit, include_hidden=include_hidden)  # type: ignore[arg-type]
        key = settings.model_dump()
        cached = await self._cached(version, f"export_{fmt}", key)
        if cached is not None:
            return await self._done_job(project, version, "export", payload, cached)
        return await self._enqueue(project, version, "export", {**payload, "key": key}, export=settings)

    async def create_render(
        self, user_id: uuid.UUID, project_id: uuid.UUID, settings: RenderSettings, version_id: uuid.UUID | None = None
    ) -> Job:
        project = await self.project(user_id, project_id)
        version = await self._version(project, version_id)
        if settings.engine == "eevee" and not self.settings.render_eevee_enabled:
            raise AppError(
                "Eevee rendering needs a GPU and is disabled on this server; use Cycles",
                code="engine_unavailable",
                status_code=422,
            )
        if max(settings.width, settings.height) > self.limits.max_render_size:
            raise AppError(
                f"Renders are limited to {self.limits.max_render_size} px", code="render_too_large", status_code=422
            )
        if settings.camera_object_id:
            ids = {o.get("object_id"): o.get("type") for o in (version.object_inventory or {}).get("objects", [])}
            if ids.get(settings.camera_object_id) != "CAMERA":
                raise AppError("That camera does not exist in this version", code="unknown_camera", status_code=422)
        payload = {
            **settings.model_dump(),
            "version_id": str(version.id),
            "version_number": version.version_number,
        }
        key = settings.model_dump()
        cached = await self._cached(version, "render", key)
        if cached is not None:
            return await self._done_job(project, version, "render", payload, cached)
        return await self._enqueue(project, version, "render", {**payload, "key": key}, render=settings)

    # ------------------------------------------------------------------ worker result
    async def complete(self, job: Job, result: BlenderJobResult) -> None:
        """Store a finished render/export as an asset of its source version."""
        staging_rel = job.payload.get("staging_dir", "")
        if not result.success:
            await asyncio.to_thread(shutil.rmtree, self.storage.resolve(staging_rel), True)
            cancelled = result.error_code == "cancelled"
            self._finish(job, "cancelled" if cancelled else "failed", result.error_message or "Blender failed")
            job.diagnostics = {**job.diagnostics, "error_code": result.error_code}
            await self.session.commit()
            await self.events.publish(
                job.id,
                "job.cancelled" if cancelled else "job.failed",
                stage=job.status,
                message=(result.error_message or "failed")[:500],
                data={"error_code": result.error_code},
            )
            return
        artifact = result.artifacts[0] if len(result.artifacts) == 1 else None
        project = await self.session.get(Project, job.project_id)
        assert project is not None
        target_rel = f"{self.storage.project_dir(project.user_id, project.id)}/derived/{job.id}"
        problem = None
        if artifact is None or artifact.kind != job.job_type:
            problem = "unexpected artifacts"
        else:
            problem = await asyncio.to_thread(
                self._promote, staging_rel, target_rel, artifact.filename, artifact.sha256
            )
        if problem:
            self._finish(job, "failed", f"Output failed verification: {problem}")
            job.diagnostics = {**job.diagnostics, "error_code": "artifact_mismatch"}
            await self.session.commit()
            await self.events.publish(job.id, "job.failed", stage="failed", message=job.error_message or "")
            return
        assert artifact is not None
        fmt = job.payload.get("format")
        asset = Asset(
            id=uuid.uuid4(),
            project_id=project.id,
            version_id=uuid.UUID(job.payload["version_id"]),
            asset_type="render" if job.job_type == "render" else f"export_{fmt}",
            file_path=f"{target_rel}/{artifact.filename}",
            file_size=artifact.size,
            checksum=artifact.sha256,
            metadata_={"key": job.payload.get("key"), "details": result.details, "job_id": str(job.id)},
        )
        self.session.add(asset)
        job.payload = {**job.payload, "asset_id": str(asset.id), "details": result.details}
        self._finish(job, "succeeded", None)
        await self.session.commit()
        await self.events.publish(
            job.id, "asset.ready", stage="done", message="Ready", data={"asset_id": str(asset.id), "kind": job.job_type}
        )
        await self.events.publish(
            job.id, "job.completed", stage="done", message="Done", data={"asset_id": str(asset.id)}
        )
        log.info("output.completed", job_id=str(job.id), kind=job.job_type, size=artifact.size)

    def _finish(self, job: Job, status: str, error: str | None) -> None:
        job.status = status
        job.error_message = error[:1000] if error else None
        job.completed_at = utcnow()
        job.progress = {"stage": "done" if status == "succeeded" else status, "message": (error or "Ready")[:200]}

    def _promote(self, staging_rel: str, target_rel: str, filename: str, sha256: str) -> str | None:
        staging = self.storage.resolve(staging_rel)
        path = staging / filename
        if not path.is_file():
            return "file missing"
        if sha256_file(path) != sha256:
            return "checksum mismatch"
        target = self.storage.resolve(target_rel)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.rmtree(target, ignore_errors=True)
        os.replace(staging, target)
        return None

    # ------------------------------------------------------------------ textures
    async def upload_texture(self, user_id: uuid.UUID, project_id: uuid.UUID, name: str, data: bytes) -> Asset:
        return await self.upload_image(user_id, project_id, name, data, "texture")

    async def upload_image(
        self, user_id: uuid.UUID, project_id: uuid.UUID, name: str, data: bytes, asset_type: str
    ) -> Asset:
        """Store a checked PNG/JPEG for the project: `texture` (DSL `image_id`) or `reference`
        (a photo attached to a chat message for image-to-3D)."""
        assert asset_type in IMAGE_ASSET_DIRS
        project = await self.project(user_id, project_id)
        if project.status != "active":
            raise ConflictError("Archived projects are read-only", code="project_archived")
        if not data:
            raise AppError("The file is empty", code="invalid_image", status_code=422)
        if len(data) > self.limits.max_texture_upload_bytes:
            raise AppError(
                f"Images are limited to {self.limits.max_texture_upload_bytes // (1024 * 1024)} MB",
                code="image_too_large",
                status_code=413,
            )
        try:
            fmt, w, h = image_info(data)
        except ImageError as exc:
            raise AppError(str(exc), code="invalid_image", status_code=422) from exc
        if not (1 <= w <= 4096 and 1 <= h <= 4096):
            raise AppError("Images must be at most 4096 × 4096 pixels", code="image_too_large", status_code=413)
        asset_id = uuid.uuid4()
        ext = "png" if fmt == "png" else "jpg"
        rel = f"{self.storage.project_dir(project.user_id, project.id)}/{IMAGE_ASSET_DIRS[asset_type]}/{asset_id}.{ext}"
        path = self.storage.resolve(rel)

        def write() -> None:
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_suffix(".partial")
            tmp.write_bytes(data)
            os.replace(tmp, path)

        await asyncio.to_thread(write)
        safe_name = "".join(c for c in Path(name or asset_type).name if c.isprintable())[:120] or asset_type
        asset = Asset(
            id=asset_id,
            project_id=project.id,
            version_id=None,
            asset_type=asset_type,
            file_path=rel,
            file_size=len(data),
            checksum=hashlib.sha256(data).hexdigest(),
            metadata_={"name": safe_name, "format": fmt, "width": w, "height": h},
        )
        self.session.add(asset)
        await self.session.commit()
        return asset

    async def textures(self, project: Project) -> list[Asset]:
        stmt = (
            select(Asset)
            .where(Asset.project_id == project.id, Asset.asset_type == "texture")
            .order_by(Asset.created_at.desc())
        )
        return list((await self.session.execute(stmt)).scalars())


async def texture_files(
    session: AsyncSession, project_id: uuid.UUID, image_ids: list[str]
) -> tuple[list[TextureFile], list[str]]:
    """Resolve DSL image ids to this project's uploaded textures: (files, unknown ids)."""
    if not image_ids:
        return [], []
    wanted = {uuid.UUID(i) for i in image_ids}
    rows = await session.execute(
        select(Asset).where(Asset.project_id == project_id, Asset.asset_type == "texture", Asset.id.in_(wanted))
    )
    found = {a.id: a for a in rows.scalars()}
    files = [TextureFile(image_id=a.id, path=a.file_path, sha256=a.checksum) for a in found.values()]
    return files, sorted(str(i) for i in wanted - set(found))
