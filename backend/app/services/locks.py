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
