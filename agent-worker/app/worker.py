"""Agent worker service: consumes `jobs:agent` (Redis Streams consumer group) and runs the LangGraph
workflow for each job, reporting through the backend's internal API. ACK only after completion.

Crash recovery: while a turn runs, the heartbeat keeps its stream entry fresh (XCLAIM JUSTID), so
only entries of a dead worker go idle. Those are reclaimed (XAUTOCLAIM) and the turn resumes from
its last LangGraph checkpoint (`/start` with reclaim=true lets the new worker take the running job).
"""

from __future__ import annotations

import signal
import threading
import time
from typing import Any

import redis
from forma3d_shared.constants.limits import ModelingLimits
from forma3d_shared.schemas.agent import AGENT_JOB_GROUP, AGENT_JOB_STREAM, AgentCompleteRequest, AgentJobSpec

from .backend_client import BackendClient, BackendUnavailable
from .checkpoint import make_checkpointer
from .config import AgentConfig
from .graph.workflow import Deps, Workflow, log
from .providers.base import LLMProvider
from .runner import AgentRunner

RECLAIM_IDLE_MS = 180_000


class Worker:
    def __init__(
        self,
        cfg: AgentConfig,
        r: redis.Redis | None = None,
        backend: BackendClient | None = None,
        providers: dict[str, LLMProvider] | None = None,
        checkpointer: Any = None,
        *,
        sleep=time.sleep,  # noqa: ANN001
    ) -> None:
        self.cfg = cfg
        self.redis = r or redis.Redis.from_url(cfg.redis_url, decode_responses=True)
        self.backend = backend or BackendClient(cfg.backend_url, cfg.internal_token)
        deps = Deps(
            backend=self.backend,
            redis=self.redis,
            providers=providers or cfg.build_providers(),
            limits=ModelingLimits.from_env(),
            max_retries=cfg.max_retries,
            max_revisions=cfg.max_revisions,
            modeling_wait_seconds=cfg.modeling_wait_seconds,
            llm_max_tokens=cfg.llm_max_tokens,
            sleep=sleep,
        )
        self.checkpointer = checkpointer if checkpointer is not None else make_checkpointer(cfg.checkpoint_url)
        self.runner = AgentRunner(
            self.redis,
            self.backend,
            Workflow(deps),
            self.checkpointer,
            worker_id=cfg.worker_id,
            step_timeout=cfg.step_timeout,
        )
        self.stop = threading.Event()
        self.current_msg: str | None = None

    def ensure_group(self) -> None:
        try:
            self.redis.xgroup_create(AGENT_JOB_STREAM, AGENT_JOB_GROUP, id="0", mkstream=True)
        except redis.ResponseError as exc:
            if "BUSYGROUP" not in str(exc):
                raise

    def touch_current(self) -> None:
        """Reset the idle time of the entry being processed so no other worker reclaims a live turn."""
        msg = self.current_msg
        if msg:
            self.redis.xclaim(
                AGENT_JOB_STREAM, AGENT_JOB_GROUP, self.cfg.worker_id, min_idle_time=0, message_ids=[msg], justid=True
            )

    def heartbeat_loop(self) -> None:
        while not self.stop.is_set():
            try:
                self.redis.set(f"workers:agent:{self.cfg.worker_id}", int(time.time()), ex=45)
                self.touch_current()
                self.cfg.liveness_file.write_text(str(int(time.time())))
            except Exception as exc:
                log("worker.heartbeat_failed", error=type(exc).__name__)
            self.stop.wait(10)

    def handle(self, msg_id: str, fields: dict[str, str], *, reclaim: bool = False) -> None:
        try:
            spec = AgentJobSpec.model_validate_json(fields["job"])
        except Exception as exc:
            log("agent.invalid_message", msg_id=msg_id, error=str(exc)[:200])
            self.redis.xack(AGENT_JOB_STREAM, AGENT_JOB_GROUP, msg_id)
            return
        self.current_msg = msg_id
        try:
            self.runner.run(spec, reclaim=reclaim)
        except BackendUnavailable:
            raise  # not ACKed -> reclaimed and resumed later
        except Exception as exc:
            log("agent.crashed", job_id=str(spec.job_id), error=f"{type(exc).__name__}: {exc}"[:300])
            try:
                self.backend.complete(
                    spec.job_id,
                    AgentCompleteRequest(
                        success=False,
                        reply="Something went wrong while working on that. The scene is unchanged.",
                        error_code="agent_error",
                        error_message=type(exc).__name__,
                    ),
                )
            except Exception:
                return  # leave un-ACKed for retry
        finally:
            self.current_msg = None
        self.redis.xack(AGENT_JOB_STREAM, AGENT_JOB_GROUP, msg_id)

    def _safe_handle(self, msg_id: str, fields: dict[str, str], *, reclaim: bool = False) -> None:
        try:
            self.handle(msg_id, fields, reclaim=reclaim)
        except BackendUnavailable as exc:
            log("agent.backend_unavailable", msg_id=msg_id, error=str(exc)[:200])
        except Exception as exc:
            log("agent.handler_error", msg_id=msg_id, error=f"{type(exc).__name__}: {exc}"[:300])

    def reclaim_pending(self, idle_ms: int = RECLAIM_IDLE_MS) -> int:
        _next, messages, *_ = self.redis.xautoclaim(
            AGENT_JOB_STREAM, AGENT_JOB_GROUP, self.cfg.worker_id, min_idle_time=idle_ms, start_id="0-0", count=10
        )
        n = 0
        for msg_id, fields in messages:
            if fields:
                log("agent.reclaimed", msg_id=msg_id)
                self._safe_handle(msg_id, fields, reclaim=True)
                n += 1
        return n

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
        log(
            "worker.started",
            worker_id=self.cfg.worker_id,
            provider=self.cfg.llm_provider,
            models={a: self.cfg.model_for(a) for a in ("conversation", "planning", "revision")},
            checkpoint=self.cfg.checkpoint_url.split("://")[0],
        )
        last_reclaim = 0.0
        while not self.stop.is_set():
            if time.monotonic() - last_reclaim > 30:
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
