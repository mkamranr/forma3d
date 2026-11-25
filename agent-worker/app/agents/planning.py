"""3D Planning Agent (LLM): task -> component plan, checked against the scene before compiling."""

from __future__ import annotations

import json
from collections.abc import Callable
from functools import lru_cache
from pathlib import Path

from forma3d_shared.constants.limits import ModelingLimits
from forma3d_shared.schemas.agent import AgentContext
from forma3d_shared.schemas.scene_object import SceneInventory
from forma3d_shared.validation import BatchValidationError, validate_batch

from ..providers.base import ChatMessage, LLMProvider
from .conversation import selection_lines
from .inspection import scene_report
from .llm import Result, structured_call
from .modeling import PlanError, check_plan, compile_plan
from .plan import Plan, plan_json_schema

PROMPTS = Path(__file__).resolve().parent.parent / "prompts"


@lru_cache
def system_prompt() -> str:
    return (PROMPTS / "planning.md").read_text(encoding="utf-8")


def build_messages(ctx: AgentContext, task: str, targets: list[str]) -> list[ChatMessage]:
    lines = [
        f"<scene>{json.dumps(scene_report(ctx.inventory), separators=(',', ':'))}</scene>",
        f"<task>{task}</task>",
    ]
    if targets:
        lines.append(f"Objects concerned: {', '.join(targets)}.")
    lines.extend(selection_lines(ctx))
    if ctx.user_message.strip() and ctx.user_message.strip() not in task:
        lines.append(f'The user\'s own words: "{ctx.user_message[:500]}"')
    lines.append("Answer with the JSON object only.")
    return [ChatMessage("system", system_prompt()), ChatMessage("user", "\n".join(lines))]


def plan_problems(plan: Plan, inv: SceneInventory, limits: ModelingLimits) -> list[str]:
    """Plan validation + DSL validation of the compiled result, phrased for the model."""
    if plan.needs_clarification:
        return [] if plan.question.strip() else ["needs_clarification is true but 'question' is empty"]
    problems = check_plan(plan, inv)
    if problems:
        return problems
    try:
        compiled = compile_plan(plan, inv)
    except PlanError as exc:
        return exc.issues
    try:
        validate_batch(compiled.batch(), inv, limits)
    except BatchValidationError as exc:
        owner = {
            op["operation_id"]: op.get("target_object_id") or op["parameters"].get("material_id", "?")
            for op in compiled.operations
        }
        return [
            f"part/material '{owner.get(i.get('operation_id') or '', '?')}': {i['message']}"
            for i in exc.as_dicts()[:15]
        ]
    return []


def make_plan(
    provider: LLMProvider,
    ctx: AgentContext,
    task: str,
    targets: list[str],
    limits: ModelingLimits,
    *,
    max_retries: int,
    max_tokens: int | None = None,
    on_retry: Callable[[int, list[str]], None] | None = None,
) -> Result[Plan]:
    return structured_call(
        "planning",
        provider,
        build_messages(ctx, task, targets),
        plan_json_schema(),
        Plan,
        check=lambda p: plan_problems(p, ctx.inventory, limits),
        max_retries=max_retries,
        max_tokens=max_tokens,
        on_retry=on_retry,
    )
