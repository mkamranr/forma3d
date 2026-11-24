"""Immutable scene versions: atomic commit, read access and crash recovery (spec §6.5, §8.6, §12).

Commit protocol
1. Verify the job still owns the project head (optimistic concurrency + project lock holder).
2. Verify every staged artifact against the worker-reported size and SHA-256.
3. Atomically rename staging/{job_id} -> versions/vNNNN (same filesystem).
4. One DB transaction: version row, assets, head-inventory rows, project head pointer, batch/job.
5. If the DB transaction fails, rename the directory back (compensation).

A crash between 3 and 4 leaves versions/vNNNN without a DB row. A retried /complete for the same
job re-adopts that directory after re-verifying checksums; otherwise the startup reconciler
moves it to quarantine/.
"""

from __future__ import annotations

import asyncio
import hashlib
import os
import shutil
import time
import uuid
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import AppError
from app.core.logging import get_logger
from app.core.time import utcnow
from app.models import Asset, Job, OperationBatch, Project, SceneObject, SceneVersion
from app.repositories.modeling import ModelingRepository
from app.services.storage import ProjectStorage
from forma3d_shared.schemas.job import REQUIRED_ARTIFACTS, BlenderJobResult

log = get_logger(__name__)


class CommitConflict(AppError):
    status_code = 409
    code = "version_conflict"


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _artifacts_match(directory: Path, result: BlenderJobResult) -> str | None:
    """Return None if every artifact matches, else a reason."""
    kinds = {a.kind for a in result.artifacts}
    missing = REQUIRED_ARTIFACTS - kinds
    if missing:
        return f"missing artifacts: {', '.join(sorted(missing))}"
    for a in result.artifacts:
        if "/" in a.filename or "\\" in a.filename or a.filename.startswith("."):
            return f"invalid artifact filename {a.filename!r}"
        path = directory / a.filename
        if not path.is_file():
            return f"{a.filename} not found"
        if path.stat().st_size != a.size or sha256_file(path) != a.sha256:
            return f"{a.filename} checksum mismatch"
    return None


def quarantine(storage: ProjectStorage, path: Path) -> Path:
    project_dir = path.parent.parent  # .../{project}/versions|staging/<name>
    target = project_dir / "quarantine" / f"{path.name}-{int(time.time())}"
    target.parent.mkdir(parents=True, exist_ok=True)
    os.replace(path, target)
    log.warning("storage.quarantined", path=str(path.relative_to(storage.root)))
    return target


class VersionService:
    def __init__(self, session: AsyncSession, storage: ProjectStorage) -> None:
        self.session = session
        self.storage = storage
        self.repo = ModelingRepository(session)

    async def commit(
        self,
        *,
        job: Job,
        project: Project,
        batch: OperationBatch | None,
        result: BlenderJobResult,
        lock_holder: str | None,
    ) -> SceneVersion:
        payload = job.payload
        expected_head = payload.get("expected_head_version_id")
        current_head = str(project.current_version_id) if project.current_version_id else None
        if current_head != expected_head:
            raise CommitConflict("The project changed while this job was running; please retry.")
        if lock_holder not in (None, str(job.id)):
            raise CommitConflict("Another modeling job holds the project lock.")
        number = int(payload["target_version_number"])
        if await self.repo.version_exists(project.id, number):
            raise CommitConflict(f"Version {number} already exists.")
        if result.inventory is None:
            raise AppError("Worker result has no scene inventory", code="invalid_result", status_code=422)

        staging = self.storage.resolve(payload["staging_dir"])
        version_rel = self.storage.version_dir(project.user_id, project.id, number)
        version_abs = self.storage.resolve(version_rel)

        def promote() -> None:
            if staging.is_dir():
                reason = _artifacts_match(staging, result)
                if reason:
                    raise AppError(
                        f"Staged artifacts failed verification: {reason}", code="artifact_mismatch", status_code=422
                    )
                if version_abs.exists():
                    quarantine(self.storage, version_abs)  # orphan from an earlier crash
                version_abs.parent.mkdir(parents=True, exist_ok=True)
                os.replace(staging, version_abs)
            elif version_abs.is_dir() and _artifacts_match(version_abs, result) is None:
                log.info("version.readopted", job_id=str(job.id), version=number)  # retried completion after crash
            else:
                raise AppError("Staged artifacts not found", code="artifact_missing", status_code=422)

        await asyncio.to_thread(promote)

        try:
            files = {a.kind: a for a in result.artifacts}
            version = SceneVersion(
                project_id=project.id,
                parent_version_id=project.current_version_id,
                version_number=number,
                blend_file_path=f"{version_rel}/{files['blend'].filename}",
                glb_file_path=f"{version_rel}/{files['glb'].filename}",
                thumbnail_path=f"{version_rel}/{files['thumbnail'].filename}" if "thumbnail" in files else None,
                operation_batch_id=job.operation_batch_id,
                message_id=uuid.UUID(payload["message_id"]) if payload.get("message_id") else None,
                object_inventory=result.inventory.model_dump(mode="json"),
                summary=(batch.summary if batch else "") or payload.get("summary", ""),
                status="committed",
            )
            self.session.add(version)
            await self.session.flush()
            self.session.add_all(
                Asset(
                    project_id=project.id,
                    version_id=version.id,
                    asset_type=a.kind,
                    file_path=f"{version_rel}/{a.filename}",
                    file_size=a.size,
                    checksum=a.sha256,
                )
                for a in result.artifacts
            )
            await self.repo.replace_scene_objects(
                project.id,
                [
                    SceneObject(
                        project_id=project.id,
                        blender_object_id=o.object_id,
                        parent_object_id=o.parent_id,
                        object_name=o.name,
                        object_type=o.type,
                        component_type=o.component_type,
                        properties=o.model_dump(
                            mode="json", exclude={"object_id", "name", "type", "component_type", "parent_id"}
                        ),
                    )
                    for o in result.inventory.objects
                ],
            )
            now = utcnow()
            project.current_version_id = version.id
            project.thumbnail_path = version.thumbnail_path or project.thumbnail_path
            project.updated_at = now
            if batch is not None:
                batch.execution_status = "succeeded"
                batch.version_id = version.id
                batch.completed_at = now
            job.status = "succeeded"
            job.completed_at = now
            job.progress = {"stage": "done", "message": f"Version {number} saved"}
            job.payload = {**payload, "result_version_id": str(version.id)}
            await self.session.commit()
        except Exception:
            await self.session.rollback()
            if version_abs.is_dir() and not staging.exists():
                await asyncio.to_thread(os.replace, version_abs, staging)  # compensate
            raise
        log.info("version.committed", project_id=str(project.id), version=number, job_id=str(job.id))
        return version

    def discard_staging(self, staging_rel: str) -> None:
        try:
            path = self.storage.resolve(staging_rel)
        except ValueError:
            return
        shutil.rmtree(path, ignore_errors=True)
        shutil.rmtree(path.with_name(path.name + ".partial"), ignore_errors=True)

    # ---------------------------------------------------------------------------------- reads
    async def list(self, project: Project) -> list[SceneVersion]:
        return await self.repo.list_versions(project.id)

    async def get(self, project: Project, version_id: uuid.UUID) -> SceneVersion | None:
        return await self.repo.get_version(project.id, version_id)


async def reconcile_storage(
    session: AsyncSession, storage: ProjectStorage, *, orphan_minutes: int = 60
) -> dict[str, int]:
    """Startup recovery: quarantine version dirs without DB rows; remove dead staging dirs."""
    stats = {"quarantined_versions": 0, "removed_staging": 0}
    projects_root = storage.root / "projects"
    if not projects_root.is_dir():
        return stats
    committed = {
        (str(pid), n)
        for pid, n in (await session.execute(select(SceneVersion.project_id, SceneVersion.version_number))).all()
    }
    active_jobs = {
        str(j.id): j
        for j in (await session.execute(select(Job).where(Job.status.in_(("queued", "running"))))).scalars()
    }
    running_targets = {
        (str(j.project_id), int(j.payload.get("target_version_number", 0))) for j in active_jobs.values()
    }
    cutoff = time.time() - orphan_minutes * 60

    for project_dir in projects_root.glob("*/*"):
        pid = project_dir.name
        for vdir in (project_dir / "versions").glob("v[0-9]*"):
            try:
                number = int(vdir.name[1:])
            except ValueError:
                continue
            if (pid, number) not in committed and (pid, number) not in running_targets:
                quarantine(storage, vdir)
                stats["quarantined_versions"] += 1
        for sdir in (project_dir / "staging").glob("*"):
            job_key = sdir.name.removesuffix(".partial")
            if job_key not in active_jobs and sdir.stat().st_mtime < cutoff:
                shutil.rmtree(sdir, ignore_errors=True)
                stats["removed_staging"] += 1
    if any(stats.values()):
        log.warning("storage.reconciled", **stats)
    return stats
