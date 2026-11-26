"""Agent trace: which LangGraph nodes ran for a turn, in which order, what each one decided, and the LLM
calls made inside them. Shown as "How this was made" in the chat and in the admin Agent activity view.

Every node is wrapped: a `running` row is recorded when it starts (so the UI can follow a turn live) and
updated in place when it ends. LLM-call and agent rows recorded while a node runs carry that node's name and
step number. Recording is best-effort: tracing never fails a turn.
"""

from __future__ import annotations

import contextvars
import time
import uuid
from collections.abc import Callable
from typing import Any

from forma3d_shared.schemas.agent import AGENT_GRAPH_KEY, AgentRunRecord
from langchain_core.runnables import RunnableConfig
from langgraph.errors import GraphInterrupt

GRAPH_KEY = AGENT_GRAPH_KEY
SEQ_KEY = "trace:seq:{job}"

#: (node, seq) of the step currently running in this thread.
current_step: contextvars.ContextVar[tuple[str, int] | None] = contextvars.ContextVar("current_step", default=None)


def _clip(value: Any, n: int = 300) -> Any:
    if isinstance(value, str):
        return value if len(value) <= n else value[: n - 1] + "…"
    return value


def summarize(name: str, update: dict[str, Any] | None, state: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    """(status, short facts) for a finished node, from the state update it returned."""
    u = update or {}
    outcome = u.get("outcome")
    out: dict[str, Any] = {}
    status = "succeeded"
    if name == "load_context":
        ctx = u.get("ctx") or {}
        inv = ctx.get("inventory") or {}
        out = {
            "message": _clip(ctx.get("user_message", ""), 200),
            "objects": len(inv.get("objects", [])),
            "selected": len(ctx.get("selected_object_ids") or []),
            "photos": len(ctx.get("reference_images") or []),
        }
    elif name == "inspect_scene":
        out = {"summary": _clip(u.get("inspection", ""), 400)}
    elif name == "vision":
        if not u:
            status, out = "skipped", {"reason": "No photo attached"}
        else:
            desc = (u.get("ctx") or {}).get("reference_description")
            out = {"description": _clip(desc or "", 600)}
    elif name == "conversation":
        d = u.get("decision") or {}
        out = {
            "intent": d.get("intent"),
            "task": _clip(d.get("task") or "", 300),
            "targets": (d.get("targets") or [])[:10],
            "reply": _clip(d.get("reply") or "", 200),
        }
    elif name == "planning":
        p = u.get("plan") or {}
        out = {
            "summary": _clip(p.get("summary") or "", 300),
            "parts": [_clip(str(part.get("name") or part.get("id") or ""), 60) for part in (p.get("parts") or [])][:25],
            "materials": len(p.get("materials") or []),
        }
        if u.get("pending_question"):
            out["question"] = _clip(u["pending_question"], 300)
    elif name == "modeling":
        c = u.get("compiled") or {}
        ops = c.get("operations") or []
        kinds: dict[str, int] = {}
        for op in ops:
            k = str(op.get("operation_type", "?"))
            kinds[k] = kinds.get(k, 0) + 1
        out = {"operations": len(ops), "by_type": dict(sorted(kinds.items(), key=lambda kv: -kv[1])[:12])}
    elif name == "execute":
        built = u.get("built")
        if built:
            out = {"result": "staged", "objects": len(built.get("objects", []))}
        elif u.get("qa"):
            status = "failed"
            out = {"result": "Blender failed", "issues": [i.get("message") for i in u["qa"].get("issues", [])][:3]}
    elif name == "quality":
        qa = u.get("qa") or {}
        issues = qa.get("issues") or []
        out = {
            "passed": qa.get("passed"),
            "checks": qa.get("checks"),
            "issues": [_clip(f"[{i.get('severity')}] {i.get('message')}", 200) for i in issues][:8],
        }
        if not qa.get("passed"):
            status = "failed"
    elif name == "revise":
        out = {"revision": u.get("revisions"), "merged": "plan" in u}
    elif name == "ask":
        out = {"question": _clip(u.get("pending_question") or "", 300)}
    elif name in ("answer", "version_manager", "commit", "fail"):
        o = outcome or {}
        meta = o.get("metadata") or {}
        out = {"reply": _clip(o.get("reply") or "", 300)}
        if meta.get("version_number"):
            out["version"] = meta["version_number"]
    elif name == "respond":
        o = state.get("outcome") or {}
        out = {"success": o.get("success", False), "reply": _clip(o.get("reply") or "", 300)}
        if o.get("error_code"):
            out["error_code"] = o["error_code"]
    if outcome and name not in ("answer", "version_manager", "commit", "respond") and not outcome.get("success"):
        status = "failed"
        out["error"] = _clip(outcome.get("error_message") or outcome.get("reply") or "", 400)
    return status, {k: v for k, v in out.items() if v not in (None, "", [], {})}


def graph_spec(compiled: Any) -> dict[str, Any]:
    """The compiled LangGraph's nodes and edges (conditional edges flagged) for the UI diagram."""
    g = compiled.get_graph()
    nodes = [{"id": nid} for nid in g.nodes]
    edges = [
        {"source": e.source, "target": e.target, "conditional": bool(e.conditional), "label": e.data or ""}
        for e in g.edges
    ]
    return {"nodes": nodes, "edges": edges}


class Tracer:
    """Wraps graph nodes; `record` posts rows to the backend (best-effort)."""

    def __init__(self, record: Callable[[uuid.UUID, AgentRunRecord], None], redis: Any, log: Callable[..., None]):
        self.record = record
        self.redis = redis
        self.log = log
        self._local_seq: dict[str, int] = {}

    def next_seq(self, job: uuid.UUID) -> int:
        key = SEQ_KEY.format(job=job)
        try:
            n = int(self.redis.incr(key))
            self.redis.expire(key, 86_400)
            return n
        except Exception:  # keep ordering within this process at least
            self._local_seq[str(job)] = self._local_seq.get(str(job), 0) + 1
            return self._local_seq[str(job)]

    def _post(self, job: uuid.UUID, rec: AgentRunRecord) -> None:
        try:
            self.record(job, rec)
        except Exception as exc:
            self.log("agent.trace_failed", job_id=str(job), error=type(exc).__name__)

    def wrap(self, name: str, fn: Callable[[Any, RunnableConfig], dict]) -> Callable[[Any, RunnableConfig], dict]:
        def node(state: Any, config: RunnableConfig) -> dict:
            job = uuid.UUID(config["configurable"]["job_id"])
            seq = self.next_seq(job)
            step_id = uuid.uuid4()
            base = {"id": step_id, "kind": "node", "node": name, "seq": seq, "agent_name": name}
            inp = {"revision": state.get("revisions", 0)} if name in ("modeling", "execute", "quality") else {}
            self._post(job, AgentRunRecord(**base, status="running", input_metadata=inp))
            token = current_step.set((name, seq))
            t0 = time.monotonic()

            def finish(status: str, out: dict | None = None, error: str | None = None) -> None:
                self._post(
                    job,
                    AgentRunRecord(
                        **base,
                        status=status,  # type: ignore[arg-type]
                        duration_ms=int((time.monotonic() - t0) * 1000),
                        input_metadata=inp,
                        output_metadata=out or {},
                        error_message=error and error[:2000],
                    ),
                )

            try:
                update = fn(state, config)
            except GraphInterrupt:
                finish("waiting", {"note": "Waiting for your answer"})
                raise
            except Exception as exc:
                cancelled = type(exc).__name__ == "Cancelled"
                finish(
                    "cancelled" if cancelled else "failed", error=None if cancelled else f"{type(exc).__name__}: {exc}"
                )
                raise
            finally:
                current_step.reset(token)
            try:
                status, out = summarize(name, update, {**dict(state), **(update or {})})
            except Exception as exc:  # a summary bug must not break the turn
                status, out = "succeeded", {"summary_error": type(exc).__name__}
            finish(status, out)
            return update

        node.__name__ = name
        return node
