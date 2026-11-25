"""Linear planner (Phase 4): one structured LLM call -> parse -> DSL validation -> bounded repair.

The LLM never executes anything. Its operations must pass the same validator the backend uses
(the backend re-validates on submission), and only validated batches are submitted."""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any

from forma3d_shared import DSL_SCHEMA_VERSION
from forma3d_shared.constants.limits import ModelingLimits
from forma3d_shared.schemas.agent import AgentContext, LLMResponse, llm_response_json_schema
from forma3d_shared.schemas.scene_object import SceneInventory
from forma3d_shared.validation import BatchValidationError, validate_batch
from pydantic import ValidationError

from .json_repair import JSONExtractError, extract_json_object
from .prompts.catalogue import operation_catalogue, operation_types
from .providers.base import ChatMessage, LLMError, LLMProvider

PROMPT_DIR = Path(__file__).parent / "prompts"


@lru_cache
def system_prompt() -> str:
    return (
        (PROMPT_DIR / "planner_system.md").read_text(encoding="utf-8").replace("{{CATALOGUE}}", operation_catalogue())
    )


def _r(v: Any) -> Any:
    if isinstance(v, float):
        return round(v, 4)
    if isinstance(v, (list, tuple)):
        return [_r(x) for x in v]
    return v


def compact_scene(inv: SceneInventory) -> dict[str, Any]:
    """Token-lean scene description: everything the planner needs to reference and place objects."""
    users: dict[str, int] = {}
    for o in inv.objects:
        for m in o.material_ids:
            users[m] = users.get(m, 0) + 1
    return {
        "objects": [
            {
                "id": o.object_id,
                "name": o.name,
                **({"component": o.component_type} if o.component_type else {}),
                **({"parent": o.parent_id} if o.parent_id else {}),
                "location": _r(o.location),
                "dimensions": _r(o.dimensions),
                **({"rotation": _r(o.rotation)} if any(abs(a) > 1e-6 for a in o.rotation) else {}),
                **({"visible": False} if not o.visible else {}),
                "materials": o.material_ids,
                **({"modifiers": [m.type.lower() for m in o.modifiers]} if o.modifiers else {}),
            }
            for o in inv.objects
        ],
        "materials": [
            {
                "id": m.material_id,
                "name": m.name,
                "color": m.base_color,
                "metallic": _r(m.metallic),
                "roughness": _r(m.roughness),
                "used_by": users.get(m.material_id, 0),
            }
            for m in inv.materials
        ],
        "collections": [c.collection_id for c in inv.collections],
    }


def build_messages(ctx: AgentContext) -> list[ChatMessage]:
    msgs = [ChatMessage("system", system_prompt())]
    for turn in ctx.history[-10:]:
        msgs.append(ChatMessage(turn.role, turn.content[:1500]))
    selected = ""
    if ctx.selected_object_id:
        obj = ctx.inventory.object_map().get(ctx.selected_object_id)
        if obj:
            selected = (
                f"\nThe user currently has '{obj.name}' (id {obj.object_id}) selected; 'it'/'this' usually means it."
            )
    version = f"version {ctx.version_number}" if ctx.version_number else "empty scene (no versions yet)"
    msgs.append(
        ChatMessage(
            "user",
            f"Project: {ctx.project_name}. Current scene ({version}):\n"
            f"<scene>{json.dumps(compact_scene(ctx.inventory), separators=(',', ':'))}</scene>{selected}\n"
            f"<request>{ctx.user_message}</request>\n"
            "Answer with the JSON object only.",
        )
    )
    return msgs


def normalize_operations(ops: list[Any]) -> list[dict[str, Any]]:
    """Fix harmless LLM formatting quirks (nulls, missing ids). Semantics are left to the validator."""
    out: list[dict[str, Any]] = []
    for i, op in enumerate(ops):
        if not isinstance(op, dict):
            out.append({"operation_id": f"op_{i + 1}", "operation_type": "invalid"})
            continue
        clean = {k: v for k, v in op.items() if v is not None}
        clean.setdefault("operation_id", f"op_{i + 1}")
        clean.setdefault("parameters", {})
        if isinstance(clean.get("parameters"), dict):
            clean["parameters"] = {k: v for k, v in clean["parameters"].items() if v is not None}
        if clean.get("dependencies") == []:
            clean.pop("dependencies")
        if clean.get("metadata") == {}:
            clean.pop("metadata")
        out.append(clean)
    return out


@dataclass
class CallRecord:
    attempt: int
    model: str
    duration_ms: int
    ok: bool
    error: str | None = None
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    issues: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class PlanResult:
    response: LLMResponse
    operations: list[dict[str, Any]]
    stats: dict[str, Any]
    calls: list[CallRecord]


class PlanningFailed(Exception):
    def __init__(self, message: str, code: str, calls: list[CallRecord]) -> None:
        super().__init__(message)
        self.code = code
        self.calls = calls


class Planner:
    def __init__(
        self,
        provider: LLMProvider,
        *,
        max_retries: int = 2,
        limits: ModelingLimits | None = None,
        max_tokens: int | None = None,
    ) -> None:
        self.provider = provider
        self.max_retries = max_retries
        self.limits = limits or ModelingLimits.from_env()
        self.max_tokens = max_tokens
        self.schema = llm_response_json_schema(operation_types())

    def plan(self, ctx: AgentContext, on_progress=lambda _msg: None) -> PlanResult:  # noqa: ANN001
        messages = build_messages(ctx)
        calls: list[CallRecord] = []
        last_problem = "the model did not return a usable plan"
        for attempt in range(1, self.max_retries + 2):
            on_progress("Planning the change" if attempt == 1 else f"Revising the plan (attempt {attempt})")
            t0 = time.monotonic()
            try:
                completion = self.provider.generate_structured(messages, self.schema, max_tokens=self.max_tokens)
            except LLMError as exc:
                calls.append(
                    CallRecord(attempt, self.provider.model, int((time.monotonic() - t0) * 1000), False, str(exc))
                )
                if not exc.retryable or attempt > self.max_retries:
                    raise PlanningFailed(f"The language model is unavailable: {exc}", "llm_unavailable", calls) from exc
                continue
            record = CallRecord(
                attempt,
                completion.model,
                int((time.monotonic() - t0) * 1000),
                True,
                prompt_tokens=completion.prompt_tokens,
                completion_tokens=completion.completion_tokens,
            )
            calls.append(record)

            feedback: str
            try:
                response = LLMResponse.model_validate(extract_json_object(completion.text))
            except (JSONExtractError, ValidationError) as exc:
                record.ok, record.error = False, f"unparseable: {str(exc)[:200]}"
                last_problem = "the model's answer was not valid JSON in the required shape"
                feedback = (
                    f"Your previous answer could not be parsed ({str(exc)[:300]}). "
                    'Reply again with exactly one JSON object: {"intent", "reply", "summary", "operations"}.'
                )
            else:
                if response.intent != "modeling":
                    return PlanResult(response, [], {}, calls)
                ops = normalize_operations(response.operations)
                if not ops:
                    record.ok, record.error = False, "modeling intent without operations"
                    last_problem = "the model did not produce any operations"
                    feedback = (
                        "intent is 'modeling' but operations is empty. Provide the operations, or use intent 'clarify'."
                    )
                else:
                    batch = {
                        "schema_version": DSL_SCHEMA_VERSION,
                        "summary": (response.summary or response.reply)[:500],
                        "operations": ops,
                    }
                    try:
                        validated = validate_batch(batch, ctx.inventory, self.limits)
                    except BatchValidationError as exc:
                        issues = exc.as_dicts()[:20]
                        record.ok, record.error, record.issues = False, "validation failed", issues
                        last_problem = "; ".join(i["message"] for i in issues[:3])
                        on_progress(f"Fixing {len(exc.issues)} validation issue(s)")
                        feedback = (
                            "The operations were REJECTED by the validator. Fix every issue and answer again with the "
                            "complete JSON (all operations, not just the fixed ones). Issues:\n"
                            + "\n".join(f"- [{i['code']}] {i.get('operation_id') or ''} {i['message']}" for i in issues)
                        )
                    else:
                        return PlanResult(response, list(validated.canonical_operations), validated.stats, calls)
            messages = [*messages, ChatMessage("assistant", completion.text[:8000]), ChatMessage("user", feedback)]
        raise PlanningFailed(
            f"I couldn't turn that into a valid change ({last_problem}). Could you rephrase or be more specific?",
            "plan_invalid",
            calls,
        )
