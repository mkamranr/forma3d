"""One agent job = one invocation of the LangGraph workflow on the conversation's thread.

- new message                 -> start a fresh turn
- answer to a clarification   -> resume the interrupted thread (`Command(resume=...)`)
- reclaimed after a crash     -> continue the same turn from its last checkpoint
"""

from __future__ import annotations

import uuid
from typing import Any

import redis
from forma3d_shared.schemas.agent import AgentCompleteRequest, AgentJobSpec
from forma3d_shared.schemas.events import cancel_flag_key
from langgraph.types import Command

from .backend_client import BackendClient, BackendUnavailable
from .graph.workflow import Cancelled, Workflow, log


class AgentRunner:
    def __init__(
        self,
        r: redis.Redis,
        backend: BackendClient,
        workflow: Workflow,
        checkpointer: Any,
        *,
        worker_id: str,
        recursion_limit: int = 80,
        step_timeout: float | None = None,
    ) -> None:
        self.redis = r
        self.backend = backend
        self.workflow = workflow
        self.graph = workflow.build(checkpointer)
        if step_timeout:
            self.graph.step_timeout = step_timeout  # node-level timeout (LLM calls, Blender wait)
        self.worker_id = worker_id
        self.recursion_limit = recursion_limit

    def run(self, spec: AgentJobSpec, *, reclaim: bool = False) -> None:
        job_id = spec.job_id
        claim = self.backend.start(job_id, self.worker_id, reclaim=reclaim)
        if not claim.proceed:
            log("agent.skipped", job_id=str(job_id), reason=claim.reason)
            return
        ctx = self.backend.context(job_id)
        thread = ctx.thread_id or f"conversation-{spec.conversation_id}"
        config = {"configurable": {"thread_id": thread, "job_id": str(job_id)}, "recursion_limit": self.recursion_limit}
        snapshot = self.graph.get_state(config)
        waiting = bool(snapshot.next) and "wait_answer" in snapshot.next
        if claim.reason == "resumed" and snapshot.next and snapshot.values.get("job_id") == str(job_id):
            inp: Any = None
            mode = "resume_after_crash"
        elif waiting:
            inp = Command(resume=ctx.user_message)
            mode = "answer_clarification"
        else:
            inp = {"job_id": str(job_id)}
            mode = "new_turn"
        log("agent.started", job_id=str(job_id), thread=thread, mode=mode)
        try:
            self.graph.invoke(inp, config, durability="sync")
        except Cancelled:
            self._cleanup(config)
            self._complete(
                job_id,
                AgentCompleteRequest(
                    success=False, reply="Cancelled — the scene is unchanged.", error_code="cancelled"
                ),
            )
        except BackendUnavailable:
            raise  # leave the message un-ACKed: it is reclaimed and the turn resumes from its checkpoint
        except Exception as exc:
            log("agent.crashed", job_id=str(job_id), error=f"{type(exc).__name__}: {exc}"[:500])
            self._cleanup(config)
            self._complete(
                job_id,
                AgentCompleteRequest(
                    success=False,
                    reply="Something went wrong while working on that. The scene is unchanged.",
                    error_code="agent_error",
                    error_message=f"{type(exc).__name__}: {exc}"[:1000],
                ),
            )
        log("agent.finished", job_id=str(job_id), mode=mode)

    def _cleanup(self, config: dict) -> None:
        """Release a staged Blender result (and the project lock) left by an aborted turn."""
        try:
            values = self.graph.get_state(config).values
            mjob = values.get("modeling_job_id")
            if mjob and values.get("built") is not None:
                self.backend.discard_staged(uuid.UUID(mjob), "The turn was aborted")
            elif mjob:
                self.redis.set(cancel_flag_key(uuid.UUID(mjob)), "1", ex=3600)
        except Exception as exc:
            log("agent.cleanup_failed", error=type(exc).__name__)

    def _complete(self, job_id: uuid.UUID, body: AgentCompleteRequest) -> None:
        self.backend.complete(job_id, body)  # idempotent on the backend
