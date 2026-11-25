"""Revision Agent (LLM): QA issues -> minimal patch to the plan (merged, recompiled, rebuilt)."""

from __future__ import annotations

import json
from dataclasses import asdict
from functools import lru_cache
from pathlib import Path

from forma3d_shared.constants.limits import ModelingLimits
from forma3d_shared.schemas.agent import AgentContext
from forma3d_shared.schemas.scene_object import SceneInventory

from ..providers.base import ChatMessage, LLMProvider
from .inspection import object_box
from .llm import Result, structured_call
from .plan import Plan, merge_revision, plan_json_schema
from .planning import plan_problems
from .quality import QAReport

PROMPTS = Path(__file__).resolve().parent.parent / "prompts"


@lru_cache
def system_prompt() -> str:
    return (PROMPTS / "revision.md").read_text(encoding="utf-8")


def build_messages(task: str, plan: Plan, report: QAReport, built: SceneInventory | None) -> list[ChatMessage]:
    involved = {i.object_id for i in report.issues if i.object_id}
    measured = []
    if built is not None:
        for o in built.objects:
            if o.object_id in involved:
                box = object_box(o)
                measured.append(
                    {
                        "id": o.object_id,
                        "position": [round(v, 4) for v in o.location],
                        "size": [round(v, 4) for v in o.dimensions],
                        "bounds": {"min": [round(v, 4) for v in box.min], "max": [round(v, 4) for v in box.max]},
                    }
                )
    issues = [
        {k: v for k, v in asdict(i).items() if v not in (None, "", {})}
        for i in report.issues
        if i.severity != "warning"
    ]
    body = "\n".join(
        [
            f"<task>{task}</task>",
            "<plan>"
            + plan.model_dump_json(exclude_none=True, exclude={"reply", "needs_clarification", "question"})
            + "</plan>",
            f"<issues>{json.dumps(issues, separators=(',', ':'))}</issues>",
            f"<measured>{json.dumps(measured, separators=(',', ':'))}</measured>" if measured else "",
            "Answer with the JSON patch only.",
        ]
    )
    return [ChatMessage("system", system_prompt()), ChatMessage("user", body)]


def revise(
    provider: LLMProvider,
    ctx: AgentContext,
    task: str,
    plan: Plan,
    report: QAReport,
    built: SceneInventory | None,
    limits: ModelingLimits,
    *,
    max_retries: int,
    max_tokens: int | None = None,
) -> tuple[Plan, Result[Plan]]:
    """Returns (merged plan, raw call result). The merged plan must still compile and validate."""

    def check(patch: Plan) -> list[str]:
        if not patch.parts and not patch.materials:
            return ["the patch is empty; change at least one part to fix the issues"]
        return plan_problems(merge_revision(plan, patch), ctx.inventory, limits)

    result = structured_call(
        "revision",
        provider,
        build_messages(task, plan, report, built),
        plan_json_schema(with_clarification=False),
        Plan,
        check=check,
        max_retries=max_retries,
        max_tokens=max_tokens,
    )
    merged = merge_revision(plan, result.value)
    # The planner's reply describes what the user asked for; a revision only fixes geometry QA found
    # ("the hub is now flush with the gas lift"), which is not news to the user.
    if result.value.reply and not plan.reply:
        merged = merged.model_copy(update={"reply": result.value.reply})
    return merged, result
