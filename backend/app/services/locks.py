"""Per-project modeling lock: at most one active modeling job per project head (spec §15.19)."""

from __future__ import annotations

import uuid

from redis.asyncio import Redis
from redis.exceptions import WatchError


def _key(project_id: uuid.UUID) -> str:
    return f"lock:project:{project_id}"


def _s(value: bytes | str | None) -> str | None:
    return value.decode() if isinstance(value, bytes) else value


class ProjectLock:
    def __init__(self, redis: Redis) -> None:
        self.redis = redis

    async def acquire(self, project_id: uuid.UUID, job_id: uuid.UUID, ttl_seconds: int) -> bool:
        return bool(await self.redis.set(_key(project_id), str(job_id), nx=True, ex=ttl_seconds))

    async def holder(self, project_id: uuid.UUID) -> str | None:
        return _s(await self.redis.get(_key(project_id)))

    async def release(self, project_id: uuid.UUID, job_id: uuid.UUID) -> bool:
        """Compare-and-delete: only the holding job may release the lock."""
        key = _key(project_id)
        async with self.redis.pipeline(transaction=True) as pipe:
            try:
                await pipe.watch(key)
                if _s(await pipe.get(key)) != str(job_id):
                    await pipe.unwatch()
                    return False
                pipe.multi()
                pipe.delete(key)
                await pipe.execute()
                return True
            except WatchError:
                return False


class AdmissionGate:
    """Short per-project mutex around "is anything running? -> create a job", so two simultaneous
    requests (chat messages, UI edits) cannot both pass the check (spec Scenario E). The second
    request waits for the first to commit and then sees its job."""

    def __init__(self, redis: Redis, project_id: uuid.UUID, *, ttl_seconds: int = 15, wait_seconds: float = 5.0):
        self.redis = redis
        self.key = f"admit:project:{project_id}"
        self.token = str(uuid.uuid4())
        self.ttl = ttl_seconds
        self.wait = wait_seconds

    async def __aenter__(self) -> AdmissionGate:
        import asyncio
        import time

        deadline = time.monotonic() + self.wait
        while not await self.redis.set(self.key, self.token, nx=True, ex=self.ttl):
            if time.monotonic() > deadline:
                from app.core.errors import ConflictError

                raise ConflictError("Another change to this project is being started", code="project_busy")
            await asyncio.sleep(0.05)
        return self

    async def __aexit__(self, *exc: object) -> None:
        if _s(await self.redis.get(self.key)) == self.token:
            await self.redis.delete(self.key)
