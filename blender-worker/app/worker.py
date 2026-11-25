"""Blender worker service: consumes `jobs:blender`, runs one Blender process per job,
streams progress events and reports structured results to the backend.

Reliability:
- Redis Streams consumer group; a message is ACKed only after the backend accepted the result.
- Messages left pending by a crashed worker are reclaimed (XAUTOCLAIM, every minute) by any live worker.
  While a job runs, the heartbeat keeps its stream entry fresh (XCLAIM JUSTID), so with several
  replicas a long render is never taken over while its worker is alive; the backend re-runs a
  reclaimed job only when the previous worker's heartbeat is gone.
- The backend's /start claim deduplicates: jobs already finished/cancelled are dropped.
- One active Blender job at a time (spec §14.4).
"""

from __future__ import annotations

import json
import signal
import threading
import time
from typing import Any

import redis
from forma3d_shared.schemas.events import JobEvent, cancel_flag_key, event_stream_key
from forma3d_shared.schemas.job import BLENDER_JOB_GROUP, BLENDER_JOB_STREAM, BlenderJobResult, BlenderJobSpec

from .backend_client import BackendClient, BackendUnavailable
from .config import WorkerConfig
from .job_runner import execute_job

STAGE_MESSAGES = {
    "loading": "Loading scene",
    "executing": "Building in Blender",
    "validating": "Checking geometry",
    "inspecting": "Inspecting result",
    "saving": "Saving project",
    "exporting": "Exporting GLB",
    "rendering": "Generating preview",
}


def log(event: str, **fields: Any) -> None:
    print(json.dumps({"event": event, "service": "blender-worker", **fields}, default=str), flush=True)


class Worker:
    def __init__(self, cfg: WorkerConfig, r: redis.Redis | None = None, backend: BackendClient | None = None) -> None:
        self.cfg = cfg
        self.redis = r or redis.Redis.from_url(cfg.redis_url, decode_responses=True)
        self.backend = backend or BackendClient(cfg.backend_url, cfg.internal_token)
        self.stop = threading.Event()
        self.current_msg: str | None = None  # stream entry being processed (kept fresh by the heartbeat)

    # ------------------------------------------------------------------ infra
    def ensure_group(self) -> None:
        try:
            self.redis.xgroup_create(BLENDER_JOB_STREAM, BLENDER_JOB_GROUP, id="0", mkstream=True)
        except redis.ResponseError as exc:
            if "BUSYGROUP" not in str(exc):
                raise

    def heartbeat_loop(self) -> None:
        while not self.stop.is_set():
            try:
                self.redis.set(f"workers:blender:{self.cfg.worker_id}", int(time.time()), ex=45)
                if self.current_msg:  # keep the in-flight entry's idle time low -> not reclaimable
                    self.redis.xclaim(
                        BLENDER_JOB_STREAM,
                        BLENDER_JOB_GROUP,
                        self.cfg.worker_id,
                        min_idle_time=0,
                        message_ids=[self.current_msg],
                        justid=True,
                    )
                self.cfg.liveness_file.write_text(str(int(time.time())))
            except Exception as exc:
                log("worker.heartbeat_failed", error=type(exc).__name__)
            self.stop.wait(10)

    def emit(self, job_id: Any, type_: str, stage: str = "", message: str = "", data: dict | None = None) -> None:
        event = JobEvent(type=type_, job_id=job_id, stage=stage, message=message, data=data or {})  # type: ignore[arg-type]
        try:
            self.redis.xadd(event_stream_key(job_id), {"event": event.model_dump_json()}, maxlen=1000, approximate=True)
        except Exception as exc:
            log("worker.emit_failed", job_id=str(job_id), error=type(exc).__name__)

    # ------------------------------------------------------------------ jobs
    def handle(self, msg_id: str, fields: dict[str, str], *, reclaim: bool = False) -> None:
        self.current_msg = msg_id
        try:
            self._handle(msg_id, fields, reclaim=reclaim)
        finally:
            self.current_msg = None

    def _handle(self, msg_id: str, fields: dict[str, str], *, reclaim: bool) -> None:
        try:
            spec = BlenderJobSpec.model_validate_json(fields["job"])
        except Exception as exc:
            log("job.invalid_message", msg_id=msg_id, error=str(exc)[:200])
            self.redis.xack(BLENDER_JOB_STREAM, BLENDER_JOB_GROUP, msg_id)
            return

        job_id = spec.job_id
        claim = self.backend.start(job_id, self.cfg.worker_id, reclaim=reclaim)
        if not claim.proceed and claim.reason == "running_elsewhere":
            log("job.left_pending", job_id=str(job_id), reason=claim.reason)  # its owner is alive
            return
        if not claim.proceed:
            log("job.skipped", job_id=str(job_id), reason=claim.reason)
            self.redis.xack(BLENDER_JOB_STREAM, BLENDER_JOB_GROUP, msg_id)
            return

        log("job.started", job_id=str(job_id), operations=len(spec.operations))
        self.emit(job_id, "blender.started", "blender", "Building in Blender")

        def on_stage(stage: str, message: str, data: dict) -> None:
            if stage == "operation":
                self.emit(job_id, "blender.progress", "operation", f"Applying {message}", data)
            elif stage in STAGE_MESSAGES:
                self.emit(job_id, "blender.progress", stage, STAGE_MESSAGES[stage], data)

        result: BlenderJobResult = execute_job(
            spec,
            self.cfg,
            emit=on_stage,
            is_cancelled=lambda: bool(self.redis.exists(cancel_flag_key(job_id))),
        )
        self.emit(
            job_id,
            "blender.completed",
            "blender",
            "Blender finished" if result.success else (result.error_message or "Blender failed"),
            {"success": result.success, "duration_ms": result.duration_ms, "error_code": result.error_code},
        )
        log(
            "job.finished",
            job_id=str(job_id),
            success=result.success,
            error_code=result.error_code,
            duration_ms=result.duration_ms,
        )
        self.backend.complete(result)  # raises BackendUnavailable -> not ACKed -> retried later
        self.redis.xack(BLENDER_JOB_STREAM, BLENDER_JOB_GROUP, msg_id)

    def reclaim_pending(self) -> None:
        idle_ms = self.cfg.reclaim_idle_seconds * 1000
        _next, messages, *_ = self.redis.xautoclaim(
            BLENDER_JOB_STREAM, BLENDER_JOB_GROUP, self.cfg.worker_id, min_idle_time=idle_ms, start_id="0-0", count=10
        )
        for msg_id, fields in messages:
            if fields:
                log("job.reclaimed", msg_id=msg_id)
                self._safe_handle(msg_id, fields, reclaim=True)

    def _safe_handle(self, msg_id: str, fields: dict[str, str], *, reclaim: bool = False) -> None:
        try:
            self.handle(msg_id, fields, reclaim=reclaim)
        except BackendUnavailable as exc:
            log("job.backend_unavailable", msg_id=msg_id, error=str(exc)[:200])
        except Exception as exc:
            log("job.handler_error", msg_id=msg_id, error=f"{type(exc).__name__}: {exc}"[:300])

    def run(self) -> None:
        self.cfg.work_root.mkdir(parents=True, exist_ok=True)
        self.ensure_group()
        threading.Thread(target=self.heartbeat_loop, daemon=True).start()
        log("worker.started", worker_id=self.cfg.worker_id, blender=self.cfg.blender_bin)
        last_reclaim = 0.0
        while not self.stop.is_set():
            if time.monotonic() - last_reclaim > 60:
                last_reclaim = time.monotonic()
                try:
                    self.reclaim_pending()
                except Exception as exc:
                    log("worker.reclaim_failed", error=type(exc).__name__)
            try:
                batches = self.redis.xreadgroup(
                    BLENDER_JOB_GROUP, self.cfg.worker_id, {BLENDER_JOB_STREAM: ">"}, count=1, block=2000
                )
            except redis.TimeoutError:
                continue  # an idle poll that ran into the socket timeout is just an empty read
            except redis.ConnectionError as exc:
                log("worker.redis_unavailable", error=str(exc)[:200])
                self.stop.wait(3)
                continue
            for _stream, messages in batches or []:
                for msg_id, fields in messages:
                    self._safe_handle(msg_id, fields)
        log("worker.stopped", worker_id=self.cfg.worker_id)


def main() -> None:
    worker = Worker(WorkerConfig.from_env())
    signal.signal(signal.SIGTERM, lambda *_: worker.stop.set())
    signal.signal(signal.SIGINT, lambda *_: worker.stop.set())
    worker.run()


if __name__ == "__main__":
    main()
