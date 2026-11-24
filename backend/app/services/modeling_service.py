"""Submitting DSL operation batches. Every path into Blender — conversational (Phase 4+),
direct UI edits and this raw endpoint — goes through `submit_batch`, so validation, locking and
versioning can never be bypassed."""

from __future__ import annotations

import uuid
from functools import lru_cache
from typing import Any

from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.errors import AppError, ConflictError, NotFoundError, ServiceUnavailableError
from app.core.logging import get_logger
from app.models import Job, OperationBatch, Project, SceneVersion
from app.repositories.modeling import ModelingRepository
from app.repositories.projects import ProjectRepository
from app.services.events import EventPublisher
from app.services.locks import ProjectLock
from app.services.storage import ProjectStorage
from forma3d_shared.constants.limits import ModelingLimits
from forma3d_shared.schemas.job import BLENDER_JOB_STREAM, BlenderJobSpec, SourceVersion
from forma3d_shared.schemas.scene_object import SceneInventory
from forma3d_shared.validation import BatchValidationError, validate_batch

log = get_logger(__name__)


@lru_cache
def get_limits() -> ModelingLimits:
    return ModelingLimits.from_env()


class ModelingService:
    def __init__(self, session: AsyncSession, redis: Redis, settings: Settings, storage: ProjectStorage) -> None:
        self.session = session
        self.redis = redis
        self.settings = settings
        self.storage = storage
        self.repo = ModelingRepository(session)
        self.events = EventPublisher(redis)
        self.lock = ProjectLock(redis)

    async def _head(self, project: Project) -> SceneVersion | None:
        if project.current_version_id is None:
            return None
        return await self.session.get(SceneVersion, project.current_version_id)

    async def head_inventory(self, project: Project) -> SceneInventory:
        head = await self._head(project)
        return SceneInventory.model_validate(head.object_inventory) if head else SceneInventory()

    async def submit_batch(
        self,
        user_id: uuid.UUID,
        project_id: uuid.UUID,
        payload: dict[str, Any],
        *,
        message_id: uuid.UUID | None = None,
    ) -> tuple[Job, OperationBatch]:
        project = await ProjectRepository(self.session).get_owned(user_id, project_id)
        if project is None:
            raise NotFoundError("Project not found")
        if project.status != "active":
            raise ConflictError("Archived projects are read-only; unarchive it first", code="project_archived")

        head = await self._head(project)
        inventory = SceneInventory.model_validate(head.object_inventory) if head else SceneInventory()
        limits = get_limits()

        try:
            validated = validate_batch(payload, inventory, limits)
        except BatchValidationError as exc:
            # Audit the rejection without persisting untrusted operation content.
            self.session.add(
                OperationBatch(
                    project_id=project.id,
                    operations=[],
                    validation_result={"valid": False, "issues": exc.as_dicts()[:50]},
                    execution_status="rejected",
                )
            )
            await self.session.commit()
            log.info("modeling.batch_rejected", project_id=str(project.id), issues=len(exc.issues))
            raise AppError(
                "The modeling operations are invalid",
                code="invalid_batch",
                status_code=422,
                extra={"issues": exc.as_dicts()[:50]},
            ) from exc

        job_id = uuid.uuid4()
        lock_ttl = self.settings.job_timeout_seconds + 600
        if not await self.lock.acquire(project.id, job_id, lock_ttl):
            raise ConflictError("Another change to this project is still being built", code="project_busy")

        try:
            source = None
            if head is not None:
                blend = await self.repo.get_asset(head.id, "blend")
                if blend is None:
                    raise AppError("Current version has no .blend file", code="corrupt_head", status_code=500)
                source = SourceVersion(
                    version_id=head.id,
                    version_number=head.version_number,
                    blend_path=blend.file_path,
                    blend_sha256=blend.checksum,
                )
            target_number = await self.repo.max_version_number(project.id) + 1
            staging_dir = self.storage.staging_dir(project.user_id, project.id, job_id)

            batch = OperationBatch(
                id=uuid.uuid4(),
                project_id=project.id,
                operations=list(validated.canonical_operations),
                validation_result={"valid": True, "stats": validated.stats},
                batch_sha256=validated.sha256,
                summary=validated.batch.summary,
                execution_status="queued",
            )
            job = Job(
                id=job_id,
                project_id=project.id,
                operation_batch_id=batch.id,
                job_type="modeling",
                status="queued",
                progress={"stage": "queued", "message": "Waiting for Blender worker"},
                payload={
                    "expected_head_version_id": str(head.id) if head else None,
                    "target_version_number": target_number,
                    "staging_dir": staging_dir,
                    "message_id": str(message_id) if message_id else None,
                    "summary": validated.batch.summary,
                },
            )
            self.session.add_all([batch, job])
            await self.session.commit()
        except Exception:
            await self.lock.release(project.id, job_id)
            raise

        spec = BlenderJobSpec(
            job_id=job.id,
            project_id=project.id,
            user_id=project.user_id,
            operation_batch_id=batch.id,
            target_version_number=target_number,
            source=source,
            staging_dir=staging_dir,
            operations=list(validated.canonical_operations),
            batch_sha256=validated.sha256,
            render_thumbnail=self.settings.render_thumbnails,
            thumbnail_size=self.settings.thumbnail_size,
            timeout_seconds=self.settings.job_timeout_seconds,
        )
        try:
            await self.redis.xadd(BLENDER_JOB_STREAM, {"job": spec.model_dump_json()})
        except Exception as exc:
            job.status, job.error_message = "failed", "Job queue unavailable"
            batch.execution_status = "failed"
            await self.session.commit()
            await self.lock.release(project.id, job_id)
            raise ServiceUnavailableError("The modeling queue is unavailable, please try again") from exc

        await self.events.publish(
            job.id, "plan.validated", stage="validated", message="Operations validated", data=validated.stats
        )
        await self.events.publish(job.id, "job.queued", stage="queued", message="Waiting for Blender worker")
        log.info("modeling.job_queued", project_id=str(project.id), job_id=str(job.id), **validated.stats)
        return job, batch
