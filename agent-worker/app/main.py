"""Agent worker entrypoint.

Phase 1 stub: connects to Redis, publishes a heartbeat and touches a liveness file used by
the container health check. LangGraph orchestration is added in Phases 4-5."""

from __future__ import annotations

import asyncio
import os
import signal
import socket
import time
from pathlib import Path

from redis.asyncio import Redis

WORKER_KIND = "agent"
HEARTBEAT_SECONDS = 10
LIVENESS_FILE = Path(os.environ.get("LIVENESS_FILE", "/tmp/worker-alive"))


async def run() -> None:
    worker_id = f"{WORKER_KIND}-{socket.gethostname()}"
    redis = Redis.from_url(os.environ.get("REDIS_URL", "redis://redis:6379/0"))
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            loop.add_signal_handler(sig, stop.set)
        except NotImplementedError:  # Windows dev shells
            pass

    print(f'{{"event":"worker.started","worker_id":"{worker_id}"}}', flush=True)
    while not stop.is_set():
        try:
            await redis.set(f"workers:{WORKER_KIND}:{worker_id}", int(time.time()), ex=HEARTBEAT_SECONDS * 3)
            LIVENESS_FILE.write_text(str(int(time.time())))
        except Exception as exc:  # keep running; health check will fail if this persists
            print(f'{{"event":"worker.heartbeat_failed","error":"{type(exc).__name__}"}}', flush=True)
        try:
            await asyncio.wait_for(stop.wait(), timeout=HEARTBEAT_SECONDS)
        except asyncio.TimeoutError:
            pass
    await redis.aclose()
    print(f'{{"event":"worker.stopped","worker_id":"{worker_id}"}}', flush=True)


if __name__ == "__main__":
    asyncio.run(run())
