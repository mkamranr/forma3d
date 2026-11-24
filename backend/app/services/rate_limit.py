"""Fixed-window rate limiting. Redis-backed in deployment, in-memory for tests/dev."""

from __future__ import annotations

import time
from typing import Protocol

from redis.asyncio import Redis


class RateLimiter(Protocol):
    async def hit(self, key: str, limit: int, window_seconds: int = 60) -> bool:
        """Record a hit; return True if allowed."""


class RedisRateLimiter:
    def __init__(self, redis: Redis) -> None:
        self.redis = redis

    async def hit(self, key: str, limit: int, window_seconds: int = 60) -> bool:
        bucket = f"ratelimit:{key}:{int(time.time() // window_seconds)}"
        async with self.redis.pipeline(transaction=True) as pipe:
            pipe.incr(bucket)
            pipe.expire(bucket, window_seconds + 1)
            count, _ = await pipe.execute()
        return int(count) <= limit


class MemoryRateLimiter:
    def __init__(self) -> None:
        self._counts: dict[str, int] = {}

    async def hit(self, key: str, limit: int, window_seconds: int = 60) -> bool:
        bucket = f"{key}:{int(time.time() // window_seconds)}"
        self._counts[bucket] = self._counts.get(bucket, 0) + 1
        return self._counts[bucket] <= limit
