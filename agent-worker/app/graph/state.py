"""Workflow state. Plain JSON-friendly dicts so every checkpoint serialises cleanly (Postgres/SQLite)."""

from __future__ import annotations

from typing import Any, TypedDict


class TurnState(TypedDict, total=False):
    job_id: str  # agent job currently driving the thread
    ctx: dict[str, Any]  # AgentContext (reloaded at the start of every turn and after a clarification)
    inspection: str  # one-line summary from the Scene Inspection Agent
    decision: dict[str, Any]  # Conversation Agent output
    clarification: dict[str, str] | None  # {"question", "original"} while waiting for / consuming an answer
    pending_question: str
    plan: dict[str, Any] | None  # Planning Agent output (patched by revisions)
    compiled: dict[str, Any] | None  # {"operations", "expectations", "summary"}
    attempt: int  # Blender attempts in this turn (idempotency key for submissions)
    revisions: int
    modeling_job_id: str | None
    built: dict[str, Any] | None  # staged inventory produced by Blender
    qa: dict[str, Any] | None
    qa_history: list[dict[str, Any]]
    outcome: dict[str, Any] | None  # what `respond` sends to the backend


def fresh_turn(job_id: str) -> TurnState:
    """Reset per-turn fields (the clarification, if any, is kept for the Conversation Agent)."""
    return TurnState(
        job_id=job_id,
        decision={},
        pending_question="",
        plan=None,
        compiled=None,
        attempt=0,
        revisions=0,
        modeling_job_id=None,
        built=None,
        qa=None,
        qa_history=[],
        outcome=None,
    )
