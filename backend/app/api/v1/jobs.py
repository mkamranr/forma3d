"""Job status, cancellation and the SSE progress stream (spec §13.5)."""

from __future__ import annotations

import asyncio
import json
import uuid
from collections.abc import AsyncIterator

from fastapi import APIRouter, Depends, Header, Request
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user, get_session, get_settings_dep
from app.core.config import Settings
from app.models import User
from app.schemas.modeling import JobOut
from app.services.job_service import JobService, job_out
from app.services.storage import ProjectStorage
from forma3d_shared.schemas.events import TERMINAL_EVENTS, event_stream_key

router = APIRouter(prefix="/jobs", tags=["jobs"])

TERMINAL_STATUSES = {"succeeded", "failed", "cancelled"}
PING_SECONDS = 15
MAX_STREAM_SECONDS = 1800


def _s(v: bytes | str) -> str:
    return v.decode() if isinstance(v, bytes) else v


def _service(request: Request, session: AsyncSession, settings: Settings) -> JobService:
    return JobService(session, request.app.state.redis, ProjectStorage(settings.data_root))


@router.get("/{job_id}", response_model=JobOut)
async def get_job(
    job_id: uuid.UUID,
    request: Request,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings_dep),
) -> JobOut:
    return job_out(await _service(request, session, settings).get_owned(user.id, job_id))


@router.post("/{job_id}/cancel", response_model=JobOut)
async def cancel_job(
    job_id: uuid.UUID,
    request: Request,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings_dep),
) -> JobOut:
    return await _service(request, session, settings).cancel(user.id, job_id)


@router.get("/{job_id}/events")
async def job_events(
    job_id: uuid.UUID,
    request: Request,
    last_event_id: str | None = Header(default=None, alias="Last-Event-ID"),
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings_dep),
) -> StreamingResponse:
    job = await _service(request, session, settings).get_owned(user.id, job_id)  # ownership check
    initial_terminal = job.status in TERMINAL_STATUSES
    redis = request.app.state.redis
    key = event_stream_key(job_id)
    start = last_event_id if last_event_id and _valid_stream_id(last_event_id) else "0-0"
    block_ms = 1000 if settings.app_env == "test" else PING_SECONDS * 1000

    async def stream() -> AsyncIterator[bytes]:
        cursor = start
        elapsed = 0.0
        yield b"retry: 3000\n\n"
        while elapsed < MAX_STREAM_SECONDS:
            if await request.is_disconnected():
                return
            entries = await redis.xread({key: cursor}, count=100, block=block_ms)
            if not entries:
                if initial_terminal:
                    return  # finished job and nothing (more) to replay
                elapsed += block_ms / 1000
                yield b": ping\n\n"
                continue
            for _stream, items in entries:
                for entry_id, fields in items:
                    cursor = _s(entry_id)
                    raw = _s(fields.get(b"event", fields.get("event", b"{}")))
                    event_type = json.loads(raw).get("type", "message")
                    yield f"id: {cursor}\nevent: {event_type}\ndata: {raw}\n\n".encode()
                    if event_type in TERMINAL_EVENTS:
                        return
            await asyncio.sleep(0)

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no", "Connection": "keep-alive"},
    )


def _valid_stream_id(value: str) -> bool:
    parts = value.split("-")
    return len(parts) == 2 and all(p.isdigit() and len(p) <= 20 for p in parts)
