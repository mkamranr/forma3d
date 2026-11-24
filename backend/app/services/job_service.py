"""Job lifecycle: worker claims (start), result handling (complete), user cancellation."""

from __future__ import annotations

import asyncio
import uuid

from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import AppError, ConflictError, NotFoundError
from app.core.logging import get_logger
from app.core.time import utcnow
from app.models import Job, Project
from app.repositories.modeling import ModelingRepository
from app.schemas.modeling import JobOut
from app.services.events import EventPublisher
from app.services.locks import ProjectLock
from app.services.storage import ProjectStorage
from app.services.version_service import VersionService
from forma3d_shared.schemas.events import cancel_flag_key
from forma3d_shared.schemas.job import BlenderJobResult, JobStartResponse

log = get_logger(__name__)


def job_out(job: Job) -> JobOut:
    rv = job.payload.get("result_version_id")
    return JobOut(
        id=job.id,
        project_id=job.project_id,
        job_type=job.job_type,
        status=job.status,
        progress=job.progress,
        error_message=job.error_message,
        error_code=job.diagnostics.get("error_code"),
        failed_operation_id=job.diagnostics.get("failed_operation_id"),
        operation_batch_id=job.operation_batch_id,
        version_id=uuid.UUID(rv) if rv else None,
        created_at=job.created_at,
        started_at=job.started_at,
        completed_at=job.completed_at,
    )


class JobService:
    def __init__(self, session: AsyncSession, redis: Redis, storage: ProjectStorage) -> None:
        self.session = session
        self.redis = redis
        self.storage = storage
        self.repo = ModelingRepository(session)
        self.events = EventPublisher(redis)
        self.lock = ProjectLock(redis)

    async def get_owned(self, user_id: uuid.UUID, job_id: uuid.UUID) -> Job:
        job = await self.repo.get_owned_job(user_id, job_id)
        if job is None:
            raise NotFoundError("Job not found")
        return job

    # ------------------------------------------------------------------ worker callbacks
    async def start(self, job_id: uuid.UUID, worker_id: str) -> JobStartResponse:
        job = await self.repo.get_job(job_id, for_update=True)
        if job is None:
            return JobStartResponse(proceed=False, reason="unknown job")
        if job.status != "queued":
            return JobStartResponse(proceed=False, reason=f"job is {job.status}")
        job.status = "running"
        job.worker_id = worker_id[:64]
        job.started_at = utcnow()
        job.progress = {"stage": "blender", "message": "Building in Blender"}
        await self.session.commit()
        await self.events.publish(job.id, "job.started", stage="started", message="Blender worker picked up the job")
        return JobStartResponse(proceed=True)

    async def complete(self, result: BlenderJobResult) -> JobOut:
        job = await self.repo.get_job(result.job_id, for_update=True)
        if job is None:
            raise NotFoundError("Job not found")
        if job.status != "running":
            # Duplicate delivery or the job was cancelled/finished meanwhile: idempotent no-op.
            log.info("job.complete_ignored", job_id=str(job.id), status=job.status)
            return job_out(job)

        project = await self.session.get(Project, job.project_id)
        batch = await self.repo.get_batch(job.operation_batch_id) if job.operation_batch_id else None
        assert project is not None
        job.diagnostics = {
            "worker_id": result.worker_id,
            "error_code": result.error_code,
            "failed_operation_id": result.failed_operation_id,
            "duration_ms": result.duration_ms,
            "blender_version": result.blender_version,
            "log_tail": result.log_tail[-4000:],
        }
        versions = VersionService(self.session, self.storage)
        try:
            if result.success:
                await self.events.publish(job.id, "agent.progress", stage="saving", message="Saving version")
                holder = await self.lock.holder(project.id)
                try:
                    version = await versions.commit(
                        job=job, project=project, batch=batch, result=result, lock_holder=holder
                    )
                except AppError as exc:
                    await self._fail(job, batch, exc.code, exc.message)
                    return job_out(job)
                await self.events.publish(
                    job.id,
                    "version.created",
                    stage="saved",
                    message=f"Version {version.version_number} saved",
                    data={"version_id": str(version.id), "version_number": version.version_number},
                )
                await self.events.publish(
                    job.id,
                    "asset.ready",
                    stage="saved",
                    message="Model ready",
                    data={"version_id": str(version.id), "kinds": [a.kind for a in result.artifacts]},
                )
                await self.events.publish(
                    job.id,
                    "job.completed",
                    stage="done",
                    message="Done",
                    data={"version_id": str(version.id), "version_number": version.version_number},
                )
            else:
                cancelled = result.error_code == "cancelled"
                await self._fail(
                    job,
                    batch,
                    result.error_code or "failed",
                    result.error_message or "Modeling failed",
                    cancelled=cancelled,
                    failed_operation_id=result.failed_operation_id,
                )
            return job_out(job)
        finally:
            await self.lock.release(project.id, job.id)
            await self.redis.delete(cancel_flag_key(job.id))

    async def _fail(
        self,
        job: Job,
        batch,
        code: str,
        message: str,
        *,
        cancelled: bool = False,
        failed_operation_id: str | None = None,
    ) -> None:
        await asyncio.to_thread(
            VersionService(self.session, self.storage).discard_staging, job.payload.get("staging_dir", "")
        )
        now = utcnow()
        job.status = "cancelled" if cancelled else "failed"
        job.error_message = message[:1000]
        job.completed_at = now
        job.progress = {"stage": job.status, "message": message[:200]}
        job.diagnostics = {**job.diagnostics, "error_code": code, "failed_operation_id": failed_operation_id}
        if batch is not None:
            batch.execution_status = job.status
            batch.completed_at = now
        await self.session.commit()
        await self.events.publish(
            job.id,
            "job.cancelled" if cancelled else "job.failed",
            stage=job.status,
            message=message[:500],
            data={"error_code": code, "failed_operation_id": failed_operation_id, "retryable": not cancelled},
        )
        log.info("job.failed", job_id=str(job.id), code=code, cancelled=cancelled)

    # ------------------------------------------------------------------ user actions
    async def cancel(self, user_id: uuid.UUID, job_id: uuid.UUID) -> JobOut:
        job = await self.get_owned(user_id, job_id)
        job = await self.repo.get_job(job.id, for_update=True)  # type: ignore[assignment]
        assert job is not None
        if job.status == "queued":
            batch = await self.repo.get_batch(job.operation_batch_id) if job.operation_batch_id else None
            await self._fail(job, batch, "cancelled", "The job was cancelled.", cancelled=True)
            await self.lock.release(job.project_id, job.id)
        elif job.status == "running":
            await self.redis.set(cancel_flag_key(job.id), "1", ex=3600)
            job.progress = {"stage": "cancelling", "message": "Cancelling…"}
            await self.session.commit()
        else:
            raise ConflictError(f"Job is already {job.status}", code="job_finished")
        return job_out(job)
