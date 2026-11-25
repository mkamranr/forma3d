"""Agent worker service: consumes `jobs:agent` (Redis Streams consumer group), runs the planner
pipeline and reports through the backend's internal API. ACK only after the job was completed."""

from __future__ import annotations

import signal
import threading
import time

import redis
from forma3d_shared.schemas.agent import AGENT_JOB_GROUP, AGENT_JOB_STREAM, AgentCompleteRequest, AgentJobSpec

from .backend_client import BackendClient, BackendUnavailable
from .config import AgentConfig
from .planner import Planner
from .runner import AgentRunner, log


class Worker:
    def __init__(
        self,
        cfg: AgentConfig,
        r: redis.Redis | None = None,
        backend: BackendClient | None = None,
        planner: Planner | None = None,
    ) -> None:
        self.cfg = cfg
        self.redis = r or redis.Redis.from_url(cfg.redis_url, decode_responses=True)
        self.backend = backend or BackendClient(cfg.backend_url, cfg.internal_token)
        self.planner = planner or Planner(
            cfg.build_provider(), max_retries=cfg.max_retries, max_tokens=cfg.llm_max_tokens
        )
        self.runner = AgentRunner(
            self.redis,
            self.backend,
            self.planner,
            worker_id=cfg.worker_id,
            modeling_wait_seconds=cfg.modeling_wait_seconds,
        )
        self.stop = threading.Event()

    def ensure_group(self) -> None:
        try:
            self.redis.xgroup_create(AGENT_JOB_STREAM, AGENT_JOB_GROUP, id="0", mkstream=True)
        except redis.ResponseError as exc:
            if "BUSYGROUP" not in str(exc):
                raise

    def heartbeat_loop(self) -> None:
        while not self.stop.is_set():
            try:
                self.redis.set(f"workers:agent:{self.cfg.worker_id}", int(time.time()), ex=45)
                self.cfg.liveness_file.write_text(str(int(time.time())))
            except Exception as exc:
                log("worker.heartbeat_failed", error=type(exc).__name__)
            self.stop.wait(10)

    def handle(self, msg_id: str, fields: dict[str, str]) -> None:
        try:
            spec = AgentJobSpec.model_validate_json(fields["job"])
        except Exception as exc:
            log("agent.invalid_message", msg_id=msg_id, error=str(exc)[:200])
            self.redis.xack(AGENT_JOB_STREAM, AGENT_JOB_GROUP, msg_id)
            return
        try:
            self.runner.run(spec)
        except BackendUnavailable:
            raise  # not ACKed -> reclaimed and retried; /start dedupes
        except Exception as exc:
            log("agent.crashed", job_id=str(spec.job_id), error=f"{type(exc).__name__}: {exc}"[:300])
            try:
                self.backend.complete(
                    spec.job_id,
                    AgentCompleteRequest(
                        success=False,
                        reply="Something went wrong while planning that change. The scene is unchanged.",
                        error_code="agent_error",
                        error_message=type(exc).__name__,
                    ),
                )
            except Exception:
                return  # leave un-ACKed for retry
        self.redis.xack(AGENT_JOB_STREAM, AGENT_JOB_GROUP, msg_id)

    def _safe_handle(self, msg_id: str, fields: dict[str, str]) -> None:
        try:
            self.handle(msg_id, fields)
        except BackendUnavailable as exc:
            log("agent.backend_unavailable", msg_id=msg_id, error=str(exc)[:200])
        except Exception as exc:
            log("agent.handler_error", msg_id=msg_id, error=f"{type(exc).__name__}: {exc}"[:300])

    def reclaim_pending(self) -> None:
        idle_ms = (self.cfg.modeling_wait_seconds + 300) * 1000
        _next, messages, *_ = self.redis.xautoclaim(
            AGENT_JOB_STREAM, AGENT_JOB_GROUP, self.cfg.worker_id, min_idle_time=idle_ms, start_id="0-0", count=10
        )
        for msg_id, fields in messages:
            if fields:
                log("agent.reclaimed", msg_id=msg_id)
                self._safe_handle(msg_id, fields)

    def run_once(self, block_ms: int = 2000) -> int:
        batches = self.redis.xreadgroup(
            AGENT_JOB_GROUP, self.cfg.worker_id, {AGENT_JOB_STREAM: ">"}, count=1, block=block_ms
        )
        n = 0
        for _stream, messages in batches or []:
            for msg_id, fields in messages:
                self._safe_handle(msg_id, fields)
                n += 1
        return n

    def run(self) -> None:
        self.ensure_group()
        threading.Thread(target=self.heartbeat_loop, daemon=True).start()
        log("worker.started", worker_id=self.cfg.worker_id, provider=self.cfg.llm_provider, model=self.cfg.llm_model)
        last_reclaim = 0.0
        while not self.stop.is_set():
            if time.monotonic() - last_reclaim > 60:
                last_reclaim = time.monotonic()
                try:
                    self.reclaim_pending()
                except Exception as exc:
                    log("worker.reclaim_failed", error=type(exc).__name__)
            try:
                self.run_once()
            except redis.TimeoutError:
                continue  # an idle poll that ran into the socket timeout is just an empty read
            except redis.ConnectionError as exc:
                log("worker.redis_unavailable", error=str(exc)[:200])
                self.stop.wait(3)
        log("worker.stopped", worker_id=self.cfg.worker_id)


def main() -> None:
    worker = Worker(AgentConfig.from_env())
    signal.signal(signal.SIGTERM, lambda *_: worker.stop.set())
    signal.signal(signal.SIGINT, lambda *_: worker.stop.set())
    worker.run()


if __name__ == "__main__":
    main()
