"""Job event publishing to Redis streams (consumed by the SSE endpoint)."""

from __future__ import annotations

import uuid
from typing import Any

from redis.asyncio import Redis

from app.core.logging import get_logger
from forma3d_shared.schemas.events import TERMINAL_EVENTS, EventType, JobEvent, event_stream_key

log = get_logger(__name__)

EVENT_STREAM_MAXLEN = 1000
EVENT_RETENTION_SECONDS = 24 * 3600


class EventPublisher:
    def __init__(self, redis: Redis) -> None:
        self.redis = redis

    async def publish(
        self,
        job_id: uuid.UUID,
        type_: EventType,
        *,
        stage: str = "",
        message: str = "",
        data: dict[str, Any] | None = None,
    ) -> None:
        event = JobEvent(type=type_, job_id=job_id, stage=stage, message=message, data=data or {})
        key = event_stream_key(job_id)
        try:
            await self.redis.xadd(key, {"event": event.model_dump_json()}, maxlen=EVENT_STREAM_MAXLEN, approximate=True)
            if type_ in TERMINAL_EVENTS:
                await self.redis.expire(key, EVENT_RETENTION_SECONDS)
        except Exception as exc:  # events are best-effort; job state lives in PostgreSQL
            log.warning("events.publish_failed", job_id=str(job_id), type=type_, error=type(exc).__name__)
