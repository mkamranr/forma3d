"""Background maintenance (Phase 8): job recovery, stale locks and retention.

Runs inside every backend process every `MAINTENANCE_INTERVAL_SECONDS`; a Redis leader key makes sure
only one process sweeps at a time, so it is safe with several backend replicas.

- **Stuck jobs**: a job that outlived its own timeout plus `JOB_GRACE_SECONDS` (its worker died and
  nobody reclaimed it, the stream entry was lost, workers were down for an hour, ...) is failed with
  `job_timeout`, its staging directory is removed and its project lock released. A stuck agent turn
  gets a failed assistant reply, so the chat never waits forever.
- **Abandoned staged results** (the agent crashed between building and committing) are discarded.
- **Stale project locks** whose holder job is finished are released (they also expire by TTL).
- **Retention**: soft-deleted projects are purged (rows cascade, files removed) after
  `DELETED_PROJECT_RETENTION_DAYS`; render/export files after `OUTPUT_RETENTION_DAYS`. Committed versions
  of live projects are never purged.
"""

from __future__ import annotations

import asyncio
import shutil
import uuid
from datetime import datetime, timedelta
from typing import Any

from redis.asyncio import Redis
from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import Settings
from app.core.logging import get_logger
from app.core.time import utcnow
from app.models import AgentRun, Asset, Job, Project
from app.services.job_service import JobService
from app.services.locks import ProjectLock
from app.services.storage import ProjectStorage
from forma3d_shared.schemas.agent import AgentCompleteRequest

log = get_logger(__name__)

LEADER_KEY = "maintenance:leader"
OUTPUT_ASSET_PREFIXES = ("render", "export_")
TIMEOUT_REPLY = "This took too long and was stopped. Please try again."


def _aware(dt: datetime | None) -> datetime | None:
    """SQLite returns naive datetimes; everything here is UTC."""
    if dt is None or dt.tzinfo is not None:
        return dt
    from datetime import timezone

    return dt.replace(tzinfo=timezone.utc)


def job_deadline(job: Job, settings: Settings) -> datetime | None:
    """When a queued/running/staged job is considered lost (None: no deadline applies)."""
    grace = timedelta(seconds=settings.job_grace_seconds)
    if job.status == "queued":
        created = _aware(job.created_at)
        return created + timedelta(seconds=settings.queued_job_timeout_seconds) if created else None
    if job.status == "staged":
        staged_at = job.payload.get("staged_at")
        since = datetime.fromisoformat(staged_at) if staged_at else _aware(job.started_at)
        return since + timedelta(seconds=settings.staged_job_timeout_seconds) if since else None
    if job.status != "running":
        return None
    started = _aware(job.started_at) or _aware(job.created_at)
    if started is None:
        return None
    if job.job_type == "agent":
        return started + timedelta(seconds=settings.agent_job_timeout_seconds)
    if job.job_type in ("render", "export"):
        return started + timedelta(seconds=settings.render_timeout_seconds) + grace
    return started + timedelta(seconds=settings.job_timeout_seconds) + grace


class Maintenance:
    def __init__(
        self,
        sessionmaker: async_sessionmaker[AsyncSession],
        redis: Redis,
        settings: Settings,
        storage: ProjectStorage,
        *,
        runtime: Any = None,
    ) -> None:
        self.sessionmaker = sessionmaker
        self.redis = redis
        self.settings = settings
        self.storage = storage
        self.runtime = runtime  # RuntimeConfig: timeouts and retention changed in the app apply here too

    # ------------------------------------------------------------------ jobs
    async def sweep_jobs(self, now: datetime | None = None) -> dict[str, int]:
        now = now or utcnow()
        stats = {"timed_out": 0, "discarded": 0, "agent_timed_out": 0}
        async with self.sessionmaker() as session:
            ids = [
                (j.id, j.job_type, j.status)
                for j in (
                    await session.execute(select(Job).where(Job.status.in_(("queued", "running", "staged"))))
                ).scalars()
                if (deadline := job_deadline(j, self.settings)) is not None and now > deadline
            ]
        for job_id, job_type, status in ids:
            try:
                async with self.sessionmaker() as session:
                    if job_type == "agent":
                        await self._fail_agent(session, job_id)
                        stats["agent_timed_out"] += 1
                    elif status == "staged":
                        await JobService(session, self.redis, self.storage).discard_staged(
                            job_id, "The result was never confirmed and was discarded."
                        )
                        stats["discarded"] += 1
                    else:
                        await self._fail_blender(session, job_id)
                        stats["timed_out"] += 1
            except Exception as exc:  # one bad row must not stop the sweep
                log.warning("maintenance.job_failed", job_id=str(job_id), error=f"{type(exc).__name__}: {exc}"[:200])
        return stats

    async def _fail_blender(self, session: AsyncSession, job_id: uuid.UUID) -> None:
        svc = JobService(session, self.redis, self.storage)
        job = await svc.repo.get_job(job_id, for_update=True)
        if job is None or job.status not in ("queued", "running"):
            return
        batch = await svc.repo.get_batch(job.operation_batch_id) if job.operation_batch_id else None
        message = (
            "No worker picked up this job; please try again."
            if job.status == "queued"
            else "The job did not finish in time and was stopped; please try again."
        )
        await svc._fail(job, batch, "job_timeout", message)
        await svc.lock.release(job.project_id, job.id)
        log.warning("maintenance.job_timeout", job_id=str(job.id), job_type=job.job_type, worker_id=job.worker_id)

    async def _fail_agent(self, session: AsyncSession, job_id: uuid.UUID) -> None:
        from app.services.conversation_service import ConversationService

        svc = ConversationService(session, self.redis, self.settings)
        message = await svc.complete(
            job_id,
            AgentCompleteRequest(
                success=False, reply=TIMEOUT_REPLY, error_code="job_timeout", error_message=TIMEOUT_REPLY
            ),
        )
        if message is not None:
            log.warning("maintenance.agent_timeout", job_id=str(job_id))

    # ------------------------------------------------------------------ locks
    async def release_stale_locks(self) -> int:
        released = 0
        async for key in self.redis.scan_iter(match="lock:project:*", count=200):
            key_s = key.decode() if isinstance(key, bytes) else key
            holder = await self.redis.get(key_s)
            holder_s = holder.decode() if isinstance(holder, bytes) else holder
            try:
                job_id = uuid.UUID(str(holder_s))
                project_id = uuid.UUID(key_s.rsplit(":", 1)[1])
            except (TypeError, ValueError):
                continue
            async with self.sessionmaker() as session:
                job = await session.get(Job, job_id)
            if job is None or job.status not in ("queued", "running", "staged"):
                if await ProjectLock(self.redis).release(project_id, job_id):
                    released += 1
                    log.warning("maintenance.lock_released", project_id=str(project_id), job_id=str(job_id))
        return released

    # ------------------------------------------------------------------ retention
    async def purge_deleted_projects(self, now: datetime | None = None) -> int:
        days = self.settings.deleted_project_retention_days
        if days <= 0:
            return 0
        cutoff = (now or utcnow()) - timedelta(days=days)
        async with self.sessionmaker() as session:
            rows = [
                p
                for p in (await session.execute(select(Project).where(Project.status == "deleted"))).scalars()
                if (deleted := _aware(p.deleted_at)) is not None and deleted < cutoff
            ]
            for p in rows:
                path = self.storage.resolve(self.storage.project_dir(p.user_id, p.id))
                await asyncio.to_thread(shutil.rmtree, path, True)
                await session.delete(p)
                log.info("maintenance.project_purged", project_id=str(p.id))
            await session.commit()
        return len(rows)

    async def purge_old_outputs(self, now: datetime | None = None) -> int:
        days = self.settings.output_retention_days
        if days <= 0:
            return 0
        cutoff = (now or utcnow()) - timedelta(days=days)
        async with self.sessionmaker() as session:
            old = [
                a
                for a in (
                    await session.execute(
                        select(Asset).where((Asset.asset_type == "render") | Asset.asset_type.like("export_%"))
                    )
                ).scalars()
                if (created := _aware(a.created_at)) is not None and created < cutoff and "/derived/" in a.file_path
            ]
            for a in old:
                path = self.storage.resolve(a.file_path).parent  # derived/{job_id}/
                await asyncio.to_thread(shutil.rmtree, path, True)
            if old:
                await session.execute(delete(Asset).where(Asset.id.in_([a.id for a in old])))
            await session.commit()
        if old:
            log.info("maintenance.outputs_purged", count=len(old))
        return len(old)

    async def purge_trace_prompts(self, now: datetime | None = None) -> int:
        """Drop the stored LLM prompts/responses of old agent traces (summaries stay)."""
        days = self.settings.agent_trace_prompt_retention_days
        if days <= 0:
            return 0
        cutoff = (now or utcnow()) - timedelta(days=days)
        async with self.sessionmaker() as session:
            res = await session.execute(
                update(AgentRun)
                .where(AgentRun.debug.is_not(None), AgentRun.created_at < cutoff)
                .values(debug=None)
                .execution_options(synchronize_session=False)
            )
            await session.commit()
        return int(res.rowcount or 0)

    # ------------------------------------------------------------------ loop
    async def run_once(self, now: datetime | None = None) -> dict[str, Any]:
        if self.runtime is not None:
            await self.runtime.refresh(self.sessionmaker, self.redis)
            self.settings = self.runtime.settings
        stats: dict[str, Any] = await self.sweep_jobs(now)
        stats["locks_released"] = await self.release_stale_locks()
        stats["projects_purged"] = await self.purge_deleted_projects(now)
        stats["outputs_purged"] = await self.purge_old_outputs(now)
        stats["trace_prompts_purged"] = await self.purge_trace_prompts(now)
        if any(stats.values()):
            log.info("maintenance.swept", **stats)
        return stats

    async def is_leader(self, owner: str) -> bool:
        """One sweeper across backend replicas: SET NX with a TTL slightly above the interval."""
        ttl = self.settings.maintenance_interval_seconds * 2
        if await self.redis.set(LEADER_KEY, owner, nx=True, ex=ttl):
            return True
        current = await self.redis.get(LEADER_KEY)
        current_s = current.decode() if isinstance(current, bytes) else current
        if current_s == owner:
            await self.redis.expire(LEADER_KEY, ttl)
            return True
        return False


async def maintenance_loop(maintenance: Maintenance, stop: asyncio.Event) -> None:
    owner = uuid.uuid4().hex
    interval = maintenance.settings.maintenance_interval_seconds
    while not stop.is_set():
        try:
            if await maintenance.is_leader(owner):
                await maintenance.run_once()
        except Exception as exc:  # keep sweeping; Redis/DB may be briefly unavailable
            log.warning("maintenance.failed", error=f"{type(exc).__name__}: {exc}"[:200])
        try:
            await asyncio.wait_for(stop.wait(), timeout=interval)
        except asyncio.TimeoutError:  # noqa: UP041 (3.10: distinct from the builtin)
            pass
