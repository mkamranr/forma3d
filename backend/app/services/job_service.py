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
from app.services.version_service import VersionService, _artifacts_match
from forma3d_shared.schemas.events import cancel_flag_key
from forma3d_shared.schemas.job import BlenderJobResult, JobStartResponse

log = get_logger(__name__)


def _staged_artifacts_problem(storage: ProjectStorage, staging_dir: str, result: BlenderJobResult) -> str | None:
    if not staging_dir:
        return "no staging directory"
    staging = storage.resolve(staging_dir)
    if not staging.is_dir():
        return "staging directory missing"
    return _artifacts_match(staging, result)


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
    async def start(self, job_id: uuid.UUID, worker_id: str, *, reclaim: bool = False) -> JobStartResponse:
        job = await self.repo.get_job(job_id, for_update=True)
        if job is None:
            return JobStartResponse(proceed=False, reason="unknown job")
        if reclaim and job.job_type == "agent" and job.status == "running":
            # The previous worker died mid-turn; the new one resumes from the LangGraph checkpoint.
            job.worker_id = worker_id[:64]
            await self.session.commit()
            log.info("job.reclaimed", job_id=str(job.id), worker_id=worker_id)
            return JobStartResponse(proceed=True, reason="resumed")
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

        if job.job_type in ("render", "export"):
            job.diagnostics = {
                "worker_id": result.worker_id,
                "error_code": result.error_code,
                "duration_ms": result.duration_ms,
                "blender_version": result.blender_version,
                "log_tail": result.log_tail[-4000:],
            }
            from app.core.config import get_settings
            from app.services.output_service import OutputService

            await OutputService(self.session, self.redis, get_settings(), self.storage).complete(job, result)
            await self.redis.delete(cancel_flag_key(job.id))
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
        keep_lock = False
        try:
            if result.success and job.payload.get("defer_commit"):
                keep_lock = await self._stage(job, batch, result)
            elif result.success:
                await self._commit(job, project, batch, result)
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
            if not keep_lock:
                await self.lock.release(project.id, job.id)
                await self.redis.delete(cancel_flag_key(job.id))

    async def _commit(self, job: Job, project: Project, batch, result: BlenderJobResult) -> bool:
        await self.events.publish(job.id, "agent.progress", stage="saving", message="Saving version")
        holder = await self.lock.holder(project.id)
        try:
            version = await VersionService(self.session, self.storage).commit(
                job=job, project=project, batch=batch, result=result, lock_holder=holder
            )
        except AppError as exc:
            await self._fail(job, batch, exc.code, exc.message)
            return False
        data = {"version_id": str(version.id), "version_number": version.version_number}
        await self.events.publish(
            job.id, "version.created", stage="saved", message=f"Version {version.version_number} saved", data=data
        )
        await self.events.publish(
            job.id,
            "asset.ready",
            stage="saved",
            message="Model ready",
            data={"version_id": str(version.id), "kinds": [a.kind for a in result.artifacts]},
        )
        await self.events.publish(job.id, "job.completed", stage="done", message="Done", data=data)
        return True

    # ------------------------------------------------------------------ staged results (agent QA)
    async def _stage(self, job: Job, batch, result: BlenderJobResult) -> bool:
        """Hold a successful result for review instead of committing it. Artifacts stay in staging
        and the project lock stays with this job until the agent commits or discards it."""
        reason = await asyncio.to_thread(
            _staged_artifacts_problem, self.storage, job.payload.get("staging_dir", ""), result
        )
        if reason or result.inventory is None:
            await self._fail(job, batch, "artifact_mismatch", f"Staged artifacts failed verification: {reason}")
            return False
        job.status = "staged"
        job.payload = {**job.payload, "staged_result": result.model_dump(mode="json")}
        job.progress = {"stage": "review", "message": "Checking the result"}
        if batch is not None:
            batch.execution_status = "staged"
        await self.session.commit()
        await self.events.publish(job.id, "job.staged", stage="review", message="Built; checking the result")
        return True

    async def staged_job(self, job_id: uuid.UUID, *, for_update: bool = False) -> Job:
        job = await self.repo.get_job(job_id, for_update=for_update)
        if job is None:
            raise NotFoundError("Job not found")
        return job

    async def commit_staged(self, job_id: uuid.UUID) -> Job:
        job = await self.staged_job(job_id, for_update=True)
        if job.status == "succeeded":
            return job  # idempotent (agent resumed after a crash)
        if job.status != "staged":
            raise ConflictError(f"Job is {job.status}", code="job_not_staged")
        project = await self.session.get(Project, job.project_id)
        batch = await self.repo.get_batch(job.operation_batch_id) if job.operation_batch_id else None
        assert project is not None
        result = BlenderJobResult.model_validate(job.payload["staged_result"])
        try:
            ok = await self._commit(job, project, batch, result)
        finally:
            await self.lock.release(project.id, job.id)
            await self.redis.delete(cancel_flag_key(job.id))
        if not ok:
            raise ConflictError(job.error_message or "The result could not be saved", code="commit_failed")
        return job

    async def discard_staged(self, job_id: uuid.UUID, reason: str) -> Job:
        job = await self.staged_job(job_id, for_update=True)
        if job.status in ("discarded", "failed", "cancelled"):
            return job
        if job.status != "staged":
            raise ConflictError(f"Job is {job.status}", code="job_not_staged")
        batch = await self.repo.get_batch(job.operation_batch_id) if job.operation_batch_id else None
        await asyncio.to_thread(
            VersionService(self.session, self.storage).discard_staging, job.payload.get("staging_dir", "")
        )
        now = utcnow()
        job.status = "discarded"
        job.completed_at = now
        job.error_message = reason[:1000]
        job.progress = {"stage": "discarded", "message": reason[:200]}
        job.payload = {k: v for k, v in job.payload.items() if k != "staged_result"}
        if batch is not None:
            batch.execution_status = "discarded"
            batch.completed_at = now
        await self.session.commit()
        await self.lock.release(job.project_id, job.id)
        await self.redis.delete(cancel_flag_key(job.id))
        await self.events.publish(
            job.id, "job.failed", stage="discarded", message=reason[:300], data={"error_code": "discarded"}
        )
        return job

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
