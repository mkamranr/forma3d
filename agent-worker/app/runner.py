"""One agent job: context -> plan -> submit -> follow the Blender job -> reply.

Events on the agent job's stream are real stage transitions; Blender progress is relayed from the
modeling job's stream so the client only follows one job."""

from __future__ import annotations

import json
import time
import uuid
from collections.abc import Callable
from typing import Any

import redis
from forma3d_shared.schemas.agent import AgentCompleteRequest, AgentJobSpec, AgentRunRecord, SubmitPlanRequest
from forma3d_shared.schemas.events import TERMINAL_EVENTS, JobEvent, cancel_flag_key, event_stream_key

from .backend_client import BackendClient, BackendRejected
from .planner import CallRecord, Planner, PlanningFailed

RELAYED = {"blender.started", "blender.progress", "blender.completed", "agent.progress", "version.created"}
BUSY_RETRIES = 6


class Cancelled(Exception):
    pass


def log(event: str, **fields: Any) -> None:
    print(json.dumps({"event": event, "service": "agent-worker", **fields}, default=str), flush=True)


class AgentRunner:
    def __init__(
        self,
        r: redis.Redis,
        backend: BackendClient,
        planner: Planner,
        *,
        worker_id: str,
        modeling_wait_seconds: int = 600,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.redis = r
        self.backend = backend
        self.planner = planner
        self.worker_id = worker_id
        self.modeling_wait_seconds = modeling_wait_seconds
        self.sleep = sleep

    # ------------------------------------------------------------------ helpers
    def emit(self, job_id: uuid.UUID, type_: str, stage: str, message: str, data: dict | None = None) -> None:
        event = JobEvent(type=type_, job_id=job_id, stage=stage, message=message, data=data or {})  # type: ignore[arg-type]
        try:
            self.redis.xadd(event_stream_key(job_id), {"event": event.model_dump_json()}, maxlen=1000, approximate=True)
        except Exception as exc:
            log("agent.emit_failed", job_id=str(job_id), error=type(exc).__name__)

    def _check_cancel(self, job_id: uuid.UUID) -> None:
        if self.redis.exists(cancel_flag_key(job_id)):
            raise Cancelled()

    def _record_calls(self, job_id: uuid.UUID, calls: list[CallRecord]) -> None:
        for c in calls:
            try:
                self.backend.record_run(
                    job_id,
                    AgentRunRecord(
                        agent_name="planner",
                        model_name=c.model[:128],
                        status="succeeded" if c.ok else "failed",
                        duration_ms=c.duration_ms,
                        input_metadata={"attempt": c.attempt, "prompt_tokens": c.prompt_tokens},
                        output_metadata={"completion_tokens": c.completion_tokens, "issues": c.issues[:10]},
                        error_message=(c.error or None) and c.error[:2000],
                    ),
                )
            except Exception as exc:  # observability must never fail the job
                log("agent.record_run_failed", job_id=str(job_id), error=type(exc).__name__)

    # ------------------------------------------------------------------ main flow
    def run(self, spec: AgentJobSpec) -> None:
        job_id = spec.job_id
        claim = self.backend.start(job_id, self.worker_id)
        if not claim.proceed:
            log("agent.skipped", job_id=str(job_id), reason=claim.reason)
            return
        log("agent.started", job_id=str(job_id))
        self.emit(job_id, "agent.started", "context", "Reading your scene")
        modeling_job_id: uuid.UUID | None = None
        try:
            ctx = self.backend.context(job_id)
            self._check_cancel(job_id)
            result = self.planner.plan(ctx, lambda msg: self.emit(job_id, "agent.progress", "planning", msg))
            self._record_calls(job_id, result.calls)
            response = result.response
            self._check_cancel(job_id)

            if response.intent != "modeling":
                self.backend.complete(
                    job_id, AgentCompleteRequest(success=True, intent=response.intent, reply=response.reply)
                )
                return

            self.emit(
                job_id,
                "plan.created",
                "planned",
                f"Planned {len(result.operations)} operation(s)",
                {"operations": len(result.operations), "summary": response.summary},
            )
            self.emit(job_id, "plan.validated", "validated", "Plan validated", result.stats)
            submitted = self._submit(job_id, response.summary or response.reply, result.operations)
            modeling_job_id = submitted.modeling_job_id
            status = self._follow(job_id, modeling_job_id)

            if status["status"] == "succeeded":
                self.backend.complete(
                    job_id,
                    AgentCompleteRequest(
                        success=True,
                        intent="modeling",
                        reply=response.reply,
                        modeling_job_id=modeling_job_id,
                    ),
                )
            elif status["status"] == "cancelled":
                raise Cancelled()
            else:
                reason = status.get("error_message") or "Blender could not apply the change"
                self.backend.complete(
                    job_id,
                    AgentCompleteRequest(
                        success=False,
                        intent="modeling",
                        modeling_job_id=modeling_job_id,
                        reply=(
                            f"I planned the change, but it could not be built: {reason} "
                            "The previous version is unchanged."
                        ),
                        error_code=status.get("error_code") or "modeling_failed",
                        error_message=reason[:1000],
                    ),
                )
        except PlanningFailed as exc:
            self._record_calls(job_id, exc.calls)
            self.backend.complete(
                job_id,
                AgentCompleteRequest(success=False, reply=str(exc), error_code=exc.code, error_message=str(exc)[:1000]),
            )
        except Cancelled:
            if modeling_job_id:
                self.redis.set(cancel_flag_key(modeling_job_id), "1", ex=3600)
            self.backend.complete(
                job_id,
                AgentCompleteRequest(
                    success=False,
                    reply="Cancelled — the scene is unchanged.",
                    error_code="cancelled",
                    modeling_job_id=modeling_job_id,
                ),
            )
        except BackendRejected as exc:
            msg = (
                "Another change is still being built — please try again in a moment."
                if exc.code == "project_busy"
                else f"The change was rejected: {exc}"
            )
            self.backend.complete(
                job_id,
                AgentCompleteRequest(
                    success=False,
                    reply=msg,
                    error_code=exc.code,
                    error_message=str(exc)[:1000],
                    modeling_job_id=modeling_job_id,
                ),
            )
        log("agent.finished", job_id=str(job_id))

    def _submit(self, job_id: uuid.UUID, summary: str, operations: list[dict[str, Any]]):
        for attempt in range(BUSY_RETRIES):
            self._check_cancel(job_id)
            try:
                return self.backend.submit_plan(job_id, SubmitPlanRequest(summary=summary[:500], operations=operations))
            except BackendRejected as exc:
                if exc.code != "project_busy" or attempt == BUSY_RETRIES - 1:
                    raise
                self.emit(job_id, "agent.progress", "waiting", "Waiting for the previous change to finish")
                self.sleep(min(2 * (attempt + 1), 10))
        raise AssertionError("unreachable")

    def _follow(self, job_id: uuid.UUID, modeling_job_id: uuid.UUID) -> dict[str, Any]:
        """Relay the modeling job's events until it finishes; the backend is the source of truth."""
        key = event_stream_key(modeling_job_id)
        cursor = "0-0"
        deadline = time.monotonic() + self.modeling_wait_seconds
        while time.monotonic() < deadline:
            if self.redis.exists(cancel_flag_key(job_id)):
                self.redis.set(cancel_flag_key(modeling_job_id), "1", ex=3600)
            try:
                entries = self.redis.xread({key: cursor}, count=50, block=2000) or []
            except redis.TimeoutError:
                entries = []
            terminal = False
            for _stream, items in entries:
                for entry_id, fields in items:
                    cursor = entry_id
                    try:
                        event = json.loads(fields.get("event", "{}"))
                    except ValueError:
                        continue
                    etype = event.get("type", "")
                    if etype in RELAYED:
                        self.emit(
                            job_id,
                            etype,
                            event.get("stage", ""),
                            event.get("message", ""),
                            {**event.get("data", {}), "modeling_job_id": str(modeling_job_id)},
                        )
                    if etype in TERMINAL_EVENTS:
                        terminal = True
            if terminal:
                break
            if not entries:
                job = self.backend.get_job(modeling_job_id)  # covers lost/expired event streams
                if job["status"] in ("succeeded", "failed", "cancelled"):
                    return job
        return self.backend.get_job(modeling_job_id)
