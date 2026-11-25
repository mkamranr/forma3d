"""Job progress events (spec §13.5). Events are real stage transitions — never fake percentages."""

from __future__ import annotations

import time
import uuid
from typing import Any, Literal

from pydantic import BaseModel, Field

EventType = Literal[
    "job.queued",
    "job.staged",  # agent turns: built, awaiting QA before commit
    "job.started",
    "agent.started",
    "agent.progress",
    "plan.created",
    "plan.validated",
    "blender.started",
    "blender.progress",
    "blender.completed",
    "quality.completed",
    "version.created",
    "asset.ready",
    "job.completed",
    "job.failed",
    "job.cancelled",
]

TERMINAL_EVENTS: frozenset[str] = frozenset({"job.completed", "job.failed", "job.cancelled"})


#: Redis stream key holding a job's events; trimmed and expired after completion.
def event_stream_key(job_id: uuid.UUID | str) -> str:
    return f"events:{job_id}"


def cancel_flag_key(job_id: uuid.UUID | str) -> str:
    return f"cancel:{job_id}"


class JobEvent(BaseModel):
    type: EventType
    job_id: uuid.UUID
    stage: str = ""
    message: str = ""
    data: dict[str, Any] = Field(default_factory=dict)
    ts: float = Field(default_factory=time.time)
