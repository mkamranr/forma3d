"""Conversation Agent (LLM): intent, self-contained task, target ids, clarification."""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any, Literal

from forma3d_shared.schemas.agent import AgentContext
from pydantic import BaseModel, ConfigDict, Field

from ..providers.base import ChatMessage, LLMProvider
from .inspection import scene_report
from .llm import Result, structured_call

PROMPTS = Path(__file__).resolve().parent.parent / "prompts"
TurnIntent = Literal["create", "modify", "question", "export", "render", "undo", "redo", "restore", "clarify"]
EXPORT_FORMATS = ("glb", "gltf", "obj", "stl")
RENDER_QUALITIES = ("draft", "standard", "high")


class Decision(BaseModel):
    model_config = ConfigDict(extra="ignore")

    intent: TurnIntent
    task: str = Field(default="", max_length=2000)
    targets: list[str] = Field(default_factory=list, max_length=200)
    version_number: int | None = None
    reply: str = Field(default="", max_length=2000)
    question: str = Field(default="", max_length=500)
    export_format: str | None = None
    render_quality: str | None = None


SCHEMA: dict[str, Any] = {
    "type": "object",
    "required": ["intent", "task", "targets", "reply"],
    "properties": {
        "intent": {"type": "string", "enum": list(TurnIntent.__args__)},  # type: ignore[attr-defined]
        "task": {"type": "string", "maxLength": 2000},
        "targets": {"type": "array", "items": {"type": "string"}, "maxItems": 200},
        "version_number": {"type": ["integer", "null"]},
        "reply": {"type": "string", "maxLength": 2000},
        "question": {"type": "string", "maxLength": 500},
        "export_format": {"type": ["string", "null"], "enum": [*EXPORT_FORMATS, None]},
        "render_quality": {"type": ["string", "null"], "enum": [*RENDER_QUALITIES, None]},
    },
}


@lru_cache
def system_prompt() -> str:
    return (PROMPTS / "conversation.md").read_text(encoding="utf-8")


def selection_lines(ctx: AgentContext) -> list[str]:
    """What the user has selected in the viewport/hierarchy (primary object last)."""
    objects = ctx.inventory.object_map()
    ids = [
        i
        for i in (ctx.selected_object_ids or ([ctx.selected_object_id] if ctx.selected_object_id else []))
        if i in objects
    ]
    if not ids:
        return []
    names = ", ".join(f"'{objects[i].name}' (id {i})" for i in ids)
    return [f"Selected object: {names}." if len(ids) == 1 else f"Selected objects: {names}."]


def build_messages(ctx: AgentContext, clarification: dict[str, str] | None) -> list[ChatMessage]:
    msgs = [ChatMessage("system", system_prompt())]
    for turn in ctx.history[-8:]:
        msgs.append(ChatMessage(turn.role, turn.content[:1200]))
    parts = [
        f"Project: {ctx.project_name}. "
        + (
            f"Current version: v{ctx.version_number}; versions: {ctx.version_numbers[:20]}."
            if ctx.version_number
            else "The scene is empty (no versions yet)."
        ),
        f"<scene>{json.dumps(scene_report(ctx.inventory), separators=(',', ':'))}</scene>",
    ]
    parts.extend(selection_lines(ctx))
    if clarification:
        parts.append(
            "<clarification>You asked: "
            + clarification["question"]
            + "\nThe original request was: "
            + clarification["original"]
            + "\nThe user's message below answers it.</clarification>"
        )
    parts.append(f"<request>{ctx.user_message}</request>")
    if ctx.reference_description:
        parts.append(f"<reference_image>{ctx.reference_description}</reference_image>")
    parts.append("Answer with the JSON object only.")
    msgs.append(ChatMessage("user", "\n".join(parts)))
    return msgs


def make_check(ctx: AgentContext):  # noqa: ANN201
    ids = set(ctx.inventory.object_map())

    def check(d: Decision) -> list[str]:
        problems = []
        unknown = [t for t in d.targets if t not in ids]
        if unknown:
            problems.append(f"targets {unknown} are not object ids in the scene; use ids from <scene> or []")
        if d.intent in ("create", "modify") and not d.task.strip():
            problems.append("intent create/modify needs a 'task' for the planner")
        if d.intent == "modify" and not ids:
            problems.append("the scene is empty, so nothing can be modified; use intent 'create'")
        if d.intent == "clarify" and not d.question.strip():
            problems.append("intent 'clarify' needs a 'question'")
        if d.intent == "restore" and d.version_number not in ctx.version_numbers:
            problems.append(f"version_number must be one of the existing versions {ctx.version_numbers[:20]}")
        if d.intent == "export" and d.export_format not in (None, *EXPORT_FORMATS):
            problems.append(f"export_format must be one of {list(EXPORT_FORMATS)}")
        if d.intent == "render" and d.render_quality not in (None, *RENDER_QUALITIES):
            problems.append(f"render_quality must be one of {list(RENDER_QUALITIES)}")
        if d.intent in ("export", "render") and not ids:
            problems.append("the scene is empty: there is nothing to export or render yet; use intent 'question'")
        if d.intent == "question" and not d.reply.strip():
            problems.append("intent 'question' needs the answer in 'reply'")
        return problems

    return check


def decide(
    provider: LLMProvider,
    ctx: AgentContext,
    clarification: dict[str, str] | None,
    *,
    max_retries: int,
    max_tokens: int | None = None,
) -> Result[Decision]:
    return structured_call(
        "conversation",
        provider,
        build_messages(ctx, clarification),
        SCHEMA,
        Decision,
        check=make_check(ctx),
        max_retries=max_retries,
        max_tokens=max_tokens or 1024,
    )
