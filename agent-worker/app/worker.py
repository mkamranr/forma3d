"""Agent worker service: consumes `jobs:agent` (Redis Streams consumer group) and runs the LangGraph
workflow for each job, reporting through the backend's internal API. ACK only after completion.

Crash recovery: while a turn runs, the heartbeat keeps its stream entry fresh (XCLAIM JUSTID), so
only entries of a dead worker go idle. Those are reclaimed (XAUTOCLAIM) and the turn resumes from
its last LangGraph checkpoint (`/start` with reclaim=true lets the new worker take the running job).
"""

from __future__ import annotations

import json
import signal
import threading
import time
from typing import Any

import httpx
import redis
from forma3d_shared.constants.limits import ModelingLimits
from forma3d_shared.runtime_config import CONFIG_VERSION_KEY, LLM_TEST_QUEUE, LLM_TEST_RESULT
from forma3d_shared.schemas.agent import AGENT_JOB_GROUP, AGENT_JOB_STREAM, AgentCompleteRequest, AgentJobSpec

from .backend_client import BackendClient, BackendUnavailable
from .checkpoint import make_checkpointer
from .config import AgentConfig
from .graph.workflow import Deps, Workflow, log
from .providers.base import LLMProvider
from .runner import AgentRunner
from .runtime import apply_overrides, encode, limits_with, run_llm_test

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
        self.base_cfg = cfg  # environment; the app's overrides are applied on top (refresh_config)
        self.config_version: int | None = None
        #: Providers injected by tests are kept; otherwise they are rebuilt when the config changes.
        self.own_providers = providers is None
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
        self._llm_checked = 0.0

    def llm_reachable(self) -> bool:
        """Cheap connectivity probe (GET {base}/models) for readiness/metrics; never sends prompts."""
        if self.cfg.llm_provider == "fake":
            return True
        headers = {"Authorization": f"Bearer {self.cfg.llm_api_key}"} if self.cfg.llm_api_key else {}
        try:
            res = httpx.get(f"{self.cfg.llm_base_url.rstrip('/')}/models", headers=headers, timeout=5.0)
            return res.status_code < 500
        except httpx.HTTPError:
            return False

    def report_llm(self) -> None:
        if time.monotonic() - self._llm_checked < 60:
            return
        self._llm_checked = time.monotonic()
        status = "ok" if self.llm_reachable() else "unavailable"
        self.redis.set(f"workers:agent:{self.cfg.worker_id}:llm", status, ex=150)
        if status != "ok":
            log("worker.llm_unreachable", base_url=self.cfg.llm_base_url)

    # ------------------------------------------------------------------ in-app configuration
    def refresh_config(self) -> None:
        """Apply settings changed in the Configuration page (checked before every job; cheap when unchanged)."""
        try:
            version = int(self.redis.get(CONFIG_VERSION_KEY) or 0)
        except Exception:
            return
        if version == self.config_version:
            return
        try:
            values = self.backend.runtime_config().get("values") or {}
        except Exception as exc:
            log("worker.config_fetch_failed", error=type(exc).__name__)
            return
        cfg = apply_overrides(self.base_cfg, values)
        d = self.runner.workflow.d
        if self.own_providers:
            try:
                d.providers = cfg.build_providers()
            except Exception as exc:  # e.g. unknown provider: keep the previous one
                log("worker.config_invalid", error=str(exc)[:200])
                return
        d.max_retries, d.max_revisions, d.llm_max_tokens = cfg.max_retries, cfg.max_revisions, cfg.llm_max_tokens
        d.limits = limits_with(values)
        self.cfg = cfg
        self._llm_checked = 0.0  # re-probe the (possibly new) endpoint soon
        if self.config_version is not None:
            log(
                "worker.config_applied",
                version=version,
                provider=cfg.llm_provider,
                models={a: cfg.model_for(a) for a in ("conversation", "planning", "revision", "vision")},
            )
        self.config_version = version

    def serve_llm_tests(self, poll_seconds: float = 1.0) -> None:
        """Answer "Test connection" from the Configuration page (only workers can reach the model).

        Polls with LPOP rather than a blocking BLPOP: one cheap command per second, and it does not tie up a
        connection or depend on client socket timeouts."""
        while not self.stop.is_set():
            try:
                raw = self.redis.lpop(LLM_TEST_QUEUE)
                if not raw:
                    self.stop.wait(poll_seconds)
                    continue
                req = json.loads(raw)
                cfg = apply_overrides(self.base_cfg, req.get("values") or {})
                try:
                    result = run_llm_test(cfg)
                except Exception as exc:
                    result = {"ok": False, "checks": [], "error": f"{type(exc).__name__}: {exc}"[:300]}
                result["worker_id"] = self.cfg.worker_id
                self.redis.set(LLM_TEST_RESULT.format(id=req["id"]), encode(result), ex=120)
            except Exception as exc:
                log("worker.llm_test_failed", error=type(exc).__name__)
                self.stop.wait(2)

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
                self.report_llm()
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
        self.refresh_config()
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
        self.refresh_config()
        threading.Thread(target=self.heartbeat_loop, daemon=True).start()
        threading.Thread(target=self.serve_llm_tests, daemon=True).start()
        log(
            "worker.started",
            worker_id=self.cfg.worker_id,
            provider=self.cfg.llm_provider,
            models={a: self.cfg.model_for(a) for a in ("conversation", "planning", "revision", "vision")},
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
