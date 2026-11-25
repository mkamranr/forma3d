"""LangGraph modeling workflow (spec §7.2).

    load_context -> inspect_scene -> conversation --(question/export)--> answer ------------------> respond
                                         |   \\--(undo/restore)--> version_manager ---------------> respond
                                         |    \\--(clarify)--> ask -> wait_answer [interrupt] -> load_context
                                         v
                                      planning --(needs clarification)--> ask
                                         v
                                      modeling (compile + DSL validation)
                                         v
                       +---------->  execute (Blender, staged) -> quality --(passed)--> commit -> respond
                       |                                            |
                       +-- modeling <-- revise <--(issues, budget)--+--(out of budget)--> fail -> respond

Every node is idempotent with respect to the backend (attempt-keyed submissions, idempotent
commit/discard/complete), so a turn can be resumed from its last checkpoint after a crash.
Only validated DSL ever reaches Blender; only a result that passed QA (or has only geometric
warnings left after the revision budget) is committed as a version.
"""

from __future__ import annotations

import json
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

import redis
from forma3d_shared.constants.limits import ModelingLimits
from forma3d_shared.schemas.agent import (
    AgentCompleteRequest,
    AgentContext,
    AgentRunRecord,
    RestoreRequest,
    SubmitPlanRequest,
)
from forma3d_shared.schemas.events import TERMINAL_EVENTS, JobEvent, cancel_flag_key, event_stream_key
from forma3d_shared.schemas.scene_object import SceneInventory
from forma3d_shared.validation import BatchValidationError, validate_batch
from langchain_core.runnables import RunnableConfig
from langgraph.graph import END, START, StateGraph
from langgraph.types import interrupt

from ..agents import conversation as conversation_agent
from ..agents import planning as planning_agent
from ..agents import revision as revision_agent
from ..agents.inspection import summarize
from ..agents.llm import AgentFailed, CallRecord
from ..agents.modeling import Expectations, PlanError, compile_plan
from ..agents.plan import Plan
from ..agents.quality import Issue, QAReport, blender_failure_report, run_qa
from ..backend_client import BackendClient, BackendRejected
from ..providers.base import LLMProvider
from .state import TurnState, fresh_turn

RELAYED = {"blender.started", "blender.progress", "blender.completed"}
STOP_EVENTS = TERMINAL_EVENTS | {"job.staged"}
BUSY_RETRIES = 6


class Cancelled(Exception):
    pass


def log(event: str, **fields: Any) -> None:
    print(json.dumps({"event": event, "service": "agent-worker", **fields}, default=str), flush=True)


@dataclass
class Deps:
    backend: BackendClient
    redis: redis.Redis
    providers: dict[str, LLMProvider]  # conversation | planning | revision
    limits: ModelingLimits = field(default_factory=ModelingLimits)
    max_retries: int = 2
    max_revisions: int = 2
    modeling_wait_seconds: int = 600
    llm_max_tokens: int | None = None
    sleep: Callable[[float], None] = time.sleep


def _job(config: RunnableConfig) -> uuid.UUID:
    return uuid.UUID(config["configurable"]["job_id"])


def _report(d: dict[str, Any]) -> QAReport:
    return QAReport(d["passed"], d["checks"], [Issue(**i) for i in d["issues"]])


def _fail_outcome(reply: str, code: str, **extra: Any) -> dict[str, Any]:
    return {"success": False, "reply": reply[:4000], "error_code": code, "error_message": reply[:1000], **extra}


class Workflow:
    def __init__(self, deps: Deps) -> None:
        self.d = deps

    # ------------------------------------------------------------------ helpers
    def emit(self, job_id: uuid.UUID, type_: str, stage: str, message: str, data: dict | None = None) -> None:
        event = JobEvent(type=type_, job_id=job_id, stage=stage, message=message, data=data or {})  # type: ignore[arg-type]
        try:
            self.d.redis.xadd(
                event_stream_key(job_id), {"event": event.model_dump_json()}, maxlen=1000, approximate=True
            )
        except Exception as exc:  # progress is best-effort
            log("agent.emit_failed", job_id=str(job_id), error=type(exc).__name__)

    def check_cancel(self, job_id: uuid.UUID) -> None:
        if self.d.redis.exists(cancel_flag_key(job_id)):
            raise Cancelled()

    def record(
        self,
        job_id: uuid.UUID,
        agent: str,
        *,
        ok: bool = True,
        duration_ms: int = 0,
        model: str | None = "deterministic",
        inp: dict | None = None,
        out: dict | None = None,
        error: str | None = None,
    ) -> None:
        try:
            self.d.backend.record_run(
                job_id,
                AgentRunRecord(
                    agent_name=agent,
                    model_name=(model or "")[:128] or None,
                    status="succeeded" if ok else "failed",
                    duration_ms=duration_ms,
                    input_metadata=inp or {},
                    output_metadata=out or {},
                    error_message=(error or None) and error[:2000],
                ),
            )
        except Exception as exc:  # observability must never fail the turn
            log("agent.record_run_failed", job_id=str(job_id), error=type(exc).__name__)

    def record_calls(self, job_id: uuid.UUID, calls: list[CallRecord]) -> None:
        for c in calls:
            self.record(
                job_id,
                c.agent,
                ok=c.ok,
                duration_ms=c.duration_ms,
                model=c.model,
                inp={"attempt": c.attempt, "prompt_tokens": c.prompt_tokens},
                out={"completion_tokens": c.completion_tokens, "issues": c.issues[:10]},
                error=c.error,
            )

    @staticmethod
    def ctx(state: TurnState) -> AgentContext:
        return AgentContext.model_validate(state["ctx"])

    # ------------------------------------------------------------------ nodes
    def load_context(self, state: TurnState, config: RunnableConfig) -> dict:
        job = _job(config)
        self.check_cancel(job)
        ctx = self.d.backend.context(job)
        return {**fresh_turn(str(job)), "ctx": ctx.model_dump(mode="json")}

    def inspect_scene(self, state: TurnState, config: RunnableConfig) -> dict:
        job = _job(config)
        t0 = time.monotonic()
        self.emit(job, "agent.started", "inspection", "Inspecting the scene", {"agent": "inspection"})
        inv = self.ctx(state).inventory
        summary = summarize(inv)
        self.record(
            job,
            "inspection",
            duration_ms=int((time.monotonic() - t0) * 1000),
            out={"objects": len(inv.objects), "materials": len(inv.materials), "summary": summary},
        )
        return {"inspection": summary}

    def conversation(self, state: TurnState, config: RunnableConfig) -> dict:
        job = _job(config)
        self.check_cancel(job)
        self.emit(job, "agent.started", "understanding", "Understanding your request", {"agent": "conversation"})
        try:
            res = conversation_agent.decide(
                self.d.providers["conversation"],
                self.ctx(state),
                state.get("clarification"),
                max_retries=self.d.max_retries,
            )
        except AgentFailed as exc:
            self.record_calls(job, exc.calls)
            return {"outcome": _fail_outcome(f"I couldn't understand that request: {exc}", exc.code)}
        self.record_calls(job, res.calls)
        d = res.value
        log("agent.decision", job_id=str(job), intent=d.intent, targets=d.targets[:10])
        return {"decision": d.model_dump(), "clarification": None}

    def answer(self, state: TurnState, config: RunnableConfig) -> dict:
        d = state["decision"]
        reply = d.get("reply") or (
            "Your model is ready — use the download button to get the GLB." if d["intent"] == "export" else "Done."
        )
        return {"outcome": {"success": True, "intent": d["intent"], "reply": reply}}

    def version_manager(self, state: TurnState, config: RunnableConfig) -> dict:
        job = _job(config)
        d = state["decision"]
        self.emit(job, "agent.progress", "versions", "Restoring a version")
        body = (
            RestoreRequest(undo=True) if d["intent"] == "undo" else RestoreRequest(version_number=d["version_number"])
        )
        try:
            res = self.d.backend.restore(job, body)
        except BackendRejected as exc:
            return {"outcome": _fail_outcome(str(exc), exc.code, intent=d["intent"])}
        reply = d.get("reply") or f"Restored version {res.version_number}."
        self.emit(
            job,
            "version.created",
            "restored",
            f"Version {res.version_number} restored",
            {"version_id": str(res.version_id), "version_number": res.version_number},
        )
        return {
            "outcome": {
                "success": True,
                "intent": d["intent"],
                "reply": reply,
                "metadata": {
                    "version_id": str(res.version_id),
                    "version_number": res.version_number,
                    "restored": res.version_number,
                },
            }
        }

    def planning(self, state: TurnState, config: RunnableConfig) -> dict:
        job = _job(config)
        self.check_cancel(job)
        d = state["decision"]
        self.emit(job, "agent.started", "planning", "Planning the design", {"agent": "planning"})
        try:
            res = planning_agent.make_plan(
                self.d.providers["planning"],
                self.ctx(state),
                d["task"],
                d.get("targets", []),
                self.d.limits,
                max_retries=self.d.max_retries,
                max_tokens=self.d.llm_max_tokens,
                on_retry=lambda _a, p: self.emit(
                    job, "agent.progress", "planning", f"Fixing {len(p)} problem(s) in the plan"
                ),
            )
        except AgentFailed as exc:
            self.record_calls(job, exc.calls)
            return {"outcome": _fail_outcome(f"{exc} Could you rephrase or be more specific?", exc.code)}
        self.record_calls(job, res.calls)
        plan = res.value
        if plan.needs_clarification:
            return {"plan": plan.model_dump(), "pending_question": plan.question}
        self.emit(
            job,
            "plan.created",
            "planned",
            f"Planned {len(plan.parts)} part change(s)",
            {"parts": len(plan.parts), "materials": len(plan.materials), "summary": plan.summary},
        )
        return {"plan": plan.model_dump()}

    def ask(self, state: TurnState, config: RunnableConfig) -> dict:
        """Reply with the question and finish this job; `wait_answer` then pauses the thread."""
        job = _job(config)
        d = state.get("decision") or {}
        question = state.get("pending_question") or d.get("question") or "Could you tell me a bit more?"
        ctx = self.ctx(state)
        original = d.get("task") or ctx.user_message
        self.d.backend.complete(job, AgentCompleteRequest(success=True, intent="clarify", reply=question))
        return {"clarification": {"question": question, "original": original[:1500]}, "pending_question": question}

    def wait_answer(self, state: TurnState, config: RunnableConfig) -> dict:
        interrupt({"question": state.get("pending_question", "")})  # resumed by the next user message
        return {"pending_question": ""}

    def modeling(self, state: TurnState, config: RunnableConfig) -> dict:
        job = _job(config)
        self.check_cancel(job)
        t0 = time.monotonic()
        inv = self.ctx(state).inventory
        self.emit(job, "agent.started", "modeling", "Compiling modeling operations", {"agent": "modeling"})
        try:
            compiled = compile_plan(Plan.model_validate(state["plan"]), inv)
            validated = validate_batch(compiled.batch(), inv, self.d.limits)
        except (PlanError, BatchValidationError) as exc:
            msg = (
                "; ".join(exc.issues)
                if isinstance(exc, PlanError)
                else "; ".join(i["message"] for i in exc.as_dicts()[:5])
            )
            self.record(job, "modeling", ok=False, error=msg)
            return {
                "outcome": _fail_outcome(f"The plan could not be turned into valid operations: {msg}", "plan_invalid")
            }
        self.record(
            job,
            "modeling",
            duration_ms=int((time.monotonic() - t0) * 1000),
            out={"operations": len(compiled.operations), **validated.stats},
        )
        self.emit(
            job, "plan.validated", "validated", f"{len(compiled.operations)} operations validated", validated.stats
        )
        return {
            "compiled": {
                "operations": list(validated.canonical_operations),
                "expectations": compiled.expectations.to_dict(),
                "summary": compiled.summary,
            }
        }

    def execute(self, state: TurnState, config: RunnableConfig) -> dict:
        job = _job(config)
        self.check_cancel(job)
        attempt = state.get("attempt", 0)
        compiled = state["compiled"]
        assert compiled is not None
        self.emit(
            job,
            "agent.progress",
            "building",
            "Building in Blender" if attempt == 0 else f"Rebuilding in Blender (revision {state['revisions']})",
        )
        body = SubmitPlanRequest(
            summary=compiled["summary"],
            operations=compiled["operations"],
            plan={"plan": state["plan"], "attempt": attempt},
            attempt=attempt,
            defer_commit=True,
        )
        for i in range(BUSY_RETRIES):
            try:
                sub = self.d.backend.submit_plan(job, body)
                break
            except BackendRejected as exc:
                if exc.code == "cancelled":
                    raise Cancelled() from exc
                if exc.code != "project_busy" or i == BUSY_RETRIES - 1:
                    return {"outcome": _fail_outcome(f"The change was rejected: {exc}", exc.code)}
                self.emit(job, "agent.progress", "waiting", "Waiting for the previous change to finish")
                self.d.sleep(min(2 * (i + 1), 10))
        mjob = sub.modeling_job_id
        status = self._follow(job, mjob)
        if status["status"] == "staged":
            built = self.d.backend.staged_inventory(mjob)
            return {"modeling_job_id": str(mjob), "built": built.model_dump(mode="json"), "qa": None}
        if status["status"] == "cancelled":
            raise Cancelled()
        reason = status.get("error_message") or "Blender could not apply the change"
        return {
            "modeling_job_id": str(mjob),
            "built": None,
            "qa": blender_failure_report(status.get("error_code") or "modeling_failed", reason).to_dict(),
        }

    def _follow(self, job: uuid.UUID, mjob: uuid.UUID) -> dict[str, Any]:
        """Relay Blender progress onto the agent job's stream until the result is staged/finished."""
        key, cursor = event_stream_key(mjob), "0-0"
        deadline = time.monotonic() + self.d.modeling_wait_seconds
        while time.monotonic() < deadline:
            if self.d.redis.exists(cancel_flag_key(job)):
                self.d.redis.set(cancel_flag_key(mjob), "1", ex=3600)
            try:
                entries = self.d.redis.xread({key: cursor}, count=50, block=2000) or []
            except redis.TimeoutError:
                entries = []
            stop = False
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
                            job,
                            etype,
                            event.get("stage", ""),
                            event.get("message", ""),
                            {**event.get("data", {}), "modeling_job_id": str(mjob)},
                        )
                    stop = stop or etype in STOP_EVENTS
            if stop or not entries:
                status = self.d.backend.get_job(mjob)  # the backend is the source of truth
                if status["status"] in ("staged", "succeeded", "failed", "cancelled", "discarded"):
                    return status
        return self.d.backend.get_job(mjob)

    def quality(self, state: TurnState, config: RunnableConfig) -> dict:
        job = _job(config)
        t0 = time.monotonic()
        self.emit(job, "agent.started", "quality", "Checking the result", {"agent": "quality"})
        if state.get("built") is None:
            report = _report(state["qa"])  # Blender failure, already expressed as a QA issue
        else:
            compiled = state["compiled"]
            assert compiled is not None
            report = run_qa(
                self.ctx(state).inventory,
                SceneInventory.model_validate(state["built"]),
                Expectations.from_dict(compiled["expectations"]),
            )
        blocking = report.errors + report.fixable
        self.record(
            job,
            "quality",
            ok=report.passed,
            duration_ms=int((time.monotonic() - t0) * 1000),
            out={
                "passed": report.passed,
                "checks": report.checks,
                "issues": [f"[{i.severity}] {i.message}" for i in report.issues][:10],
            },
        )
        message = (
            f"Quality checks passed ({report.checks} checks)"
            if report.passed
            else f"Quality check found {len(blocking)} issue(s): {blocking[0].message}"
        )
        self.emit(
            job,
            "quality.completed",
            "quality",
            message,
            {"passed": report.passed, "checks": report.checks, "issues": len(blocking)},
        )
        history = [*state.get("qa_history", []), {"passed": report.passed, "issues": len(blocking)}]
        return {"qa": report.to_dict(), "qa_history": history}

    def revise(self, state: TurnState, config: RunnableConfig) -> dict:
        job = _job(config)
        self.check_cancel(job)
        report = _report(state["qa"])
        first = (report.errors + report.fixable)[0]
        self.emit(job, "agent.started", "revision", f"Fixing: {first.message}"[:200], {"agent": "revision"})
        built = SceneInventory.model_validate(state["built"]) if state.get("built") else None
        try:
            merged, res = revision_agent.revise(
                self.d.providers["revision"],
                self.ctx(state),
                state["decision"].get("task", ""),
                Plan.model_validate(state["plan"]),
                report,
                built,
                self.d.limits,
                max_retries=self.d.max_retries,
                max_tokens=self.d.llm_max_tokens,
            )
        except AgentFailed as exc:
            self.record_calls(job, exc.calls)
            log("agent.revision_failed", job_id=str(job), error=str(exc)[:200])
            return {"revisions": self.d.max_revisions}  # budget exhausted: route to commit/fail
        self.record_calls(job, res.calls)
        if state.get("modeling_job_id") and built is not None:
            self.d.backend.discard_staged(uuid.UUID(state["modeling_job_id"]), f"Revised: {first.message}")
        return {
            "plan": merged.model_dump(),
            "revisions": state.get("revisions", 0) + 1,
            "attempt": state.get("attempt", 0) + 1,
            "built": None,
            "modeling_job_id": None,
            "compiled": None,
        }

    def commit(self, state: TurnState, config: RunnableConfig) -> dict:
        job = _job(config)
        report = _report(state["qa"])
        mjob = uuid.UUID(state["modeling_job_id"])  # type: ignore[arg-type]
        try:
            res = self.d.backend.commit_staged(mjob)
        except BackendRejected as exc:
            return {"outcome": _fail_outcome(f"The result could not be saved: {exc}", exc.code)}
        self.emit(
            job,
            "version.created",
            "saved",
            f"Version {res.version_number} saved",
            {"version_id": str(res.version_id), "version_number": res.version_number},
        )
        plan = Plan.model_validate(state["plan"])
        warnings = [i.message for i in report.issues if i.severity in ("fixable", "warning")]
        reply = plan.reply or "Done."
        if not report.passed and warnings:
            reply += f" Note: {warnings[0]}."
        quality = {
            "passed": report.passed,
            "checks": report.checks,
            "revisions": state.get("revisions", 0),
            "warnings": warnings[:5],
        }
        return {
            "outcome": {
                "success": True,
                "intent": "modeling",
                "reply": reply,
                "modeling_job_id": str(mjob),
                "metadata": {"quality": quality},
            }
        }

    def fail(self, state: TurnState, config: RunnableConfig) -> dict:
        report = _report(state["qa"])
        if state.get("modeling_job_id") and state.get("built") is not None:
            self.d.backend.discard_staged(uuid.UUID(state["modeling_job_id"]), "Quality checks failed")
        problems = [i.message for i in report.errors + report.fixable][:3]
        reply = (
            "I couldn't build that correctly"
            + (f" after {state.get('revisions', 0)} revision(s)" if state.get("revisions") else "")
            + ": "
            + "; ".join(problems)
            + ". The scene is unchanged."
        )
        quality = {
            "passed": False,
            "checks": report.checks,
            "revisions": state.get("revisions", 0),
            "warnings": problems,
        }
        return {"outcome": _fail_outcome(reply, "quality_failed", intent="modeling", metadata={"quality": quality})}

    def respond(self, state: TurnState, config: RunnableConfig) -> dict:
        outcome = dict(state.get("outcome") or _fail_outcome("Something went wrong.", "agent_error"))
        intent = outcome.pop("intent", None)
        if intent in ("create", "modify"):
            intent = "modeling"
        self.d.backend.complete(_job(config), AgentCompleteRequest(intent=intent, **outcome))
        return {}

    # ------------------------------------------------------------------ routing
    @staticmethod
    def after_conversation(state: TurnState) -> str:
        if state.get("outcome"):
            return "respond"
        intent = state["decision"]["intent"]
        return {
            "question": "answer",
            "export": "answer",
            "undo": "version_manager",
            "restore": "version_manager",
            "clarify": "ask",
        }.get(intent, "planning")

    @staticmethod
    def after_planning(state: TurnState) -> str:
        if state.get("outcome"):
            return "respond"
        return "ask" if state.get("pending_question") else "modeling"

    @staticmethod
    def after_step(state: TurnState) -> str:
        return "respond" if state.get("outcome") else "next"

    def after_quality(self, state: TurnState) -> str:
        report = _report(state["qa"])
        if report.passed:
            return "commit"
        if state.get("revisions", 0) < self.d.max_revisions:
            return "revise"
        return self._settle(state, report)

    def after_revise(self, state: TurnState) -> str:
        if state.get("compiled") is None and state.get("built") is None and state.get("modeling_job_id") is None:
            return "modeling"  # a revision was merged
        return self._settle(state, _report(state["qa"]))

    @staticmethod
    def _settle(state: TurnState, report: QAReport) -> str:
        """Out of revisions: keep a result whose only problems are geometric warnings, else roll back."""
        return "commit" if state.get("built") is not None and not report.errors else "fail"

    # ------------------------------------------------------------------ graph
    def build(self, checkpointer: Any):
        g = StateGraph(TurnState)
        for name in (
            "load_context",
            "inspect_scene",
            "conversation",
            "answer",
            "version_manager",
            "planning",
            "ask",
            "wait_answer",
            "modeling",
            "execute",
            "quality",
            "revise",
            "commit",
            "fail",
            "respond",
        ):
            g.add_node(name, getattr(self, name))
        g.add_edge(START, "load_context")
        g.add_edge("load_context", "inspect_scene")
        g.add_edge("inspect_scene", "conversation")
        g.add_conditional_edges(
            "conversation", self.after_conversation, ["answer", "version_manager", "ask", "planning", "respond"]
        )
        g.add_edge("answer", "respond")
        g.add_edge("version_manager", "respond")
        g.add_conditional_edges("planning", self.after_planning, ["ask", "modeling", "respond"])
        g.add_edge("ask", "wait_answer")
        g.add_edge("wait_answer", "load_context")
        g.add_conditional_edges("modeling", self.after_step, {"next": "execute", "respond": "respond"})
        g.add_conditional_edges("execute", self.after_step, {"next": "quality", "respond": "respond"})
        g.add_conditional_edges("quality", self.after_quality, ["commit", "revise", "fail"])
        g.add_conditional_edges("revise", self.after_revise, ["modeling", "commit", "fail"])
        g.add_edge("commit", "respond")
        g.add_edge("fail", "respond")
        g.add_edge("respond", END)
        return g.compile(checkpointer=checkpointer)
