"""Deterministic provider for CI, demos and local development without an LLM server.

It answers the spec §23 vertical-slice prompts (desk -> black metal legs -> two drawers -> walnut
top -> export) with hand-written plans that target the ids it created, and falls back to a
clarifying reply otherwise. It reads the scene inventory from the prompt like a real model would,
so edits only reference objects that exist."""

from __future__ import annotations

import json
import re
from typing import Any

from .base import ChatMessage, Completion

DESK_OPS: list[dict[str, Any]] = [
    {
        "operation_id": "op_col",
        "operation_type": "create_collection",
        "parameters": {"collection_id": "desk", "name": "Desk"},
    },
    {
        "operation_id": "op_oak",
        "operation_type": "create_material",
        "parameters": {"material_id": "oak", "name": "Oak", "base_color": "#C99563", "roughness": 0.6},
    },
    {
        "operation_id": "op_frame",
        "operation_type": "create_material",
        "parameters": {
            "material_id": "graphite",
            "name": "Graphite",
            "base_color": "#424953",
            "metallic": 0.6,
            "roughness": 0.4,
        },
    },
    {
        "operation_id": "op_top",
        "operation_type": "create_cube",
        "target_object_id": "desk_top",
        "dependencies": ["op_col", "op_oak"],
        "parameters": {
            "name": "Desk Top",
            "dimensions": [1.6, 0.8, 0.04],
            "location": [0, 0, 0.74],
            "material_id": "oak",
            "collection_id": "desk",
        },
        "metadata": {"component": "tabletop"},
    },
    {
        "operation_id": "op_bevel",
        "operation_type": "bevel",
        "target_object_id": "desk_top",
        "dependencies": ["op_top"],
        "parameters": {"width": 0.006, "segments": 3},
    },
] + [
    {
        "operation_id": f"op_{lid}",
        "operation_type": "create_cube",
        "target_object_id": lid,
        "dependencies": ["op_top", "op_frame"],
        "parameters": {
            "name": name,
            "dimensions": [0.05, 0.05, 0.72],
            "location": [x, y, 0.36],
            "material_id": "graphite",
            "parent_id": "desk_top",
            "collection_id": "desk",
        },
        "metadata": {"component": "leg"},
    }
    for lid, name, x, y in [
        ("leg_fl", "Leg Front Left", -0.75, -0.35),
        ("leg_fr", "Leg Front Right", 0.75, -0.35),
        ("leg_bl", "Leg Back Left", -0.75, 0.35),
        ("leg_br", "Leg Back Right", 0.75, 0.35),
    ]
]

BLACK_LEGS_OPS = [
    {
        "operation_id": "op_black",
        "operation_type": "create_material",
        "parameters": {
            "material_id": "black_metal",
            "name": "Black Metal",
            "base_color": "#111111",
            "metallic": 1.0,
            "roughness": 0.35,
        },
    },
] + [
    {
        "operation_id": f"op_assign_{lid}",
        "operation_type": "assign_material",
        "target_object_id": lid,
        "dependencies": ["op_black"],
        "parameters": {"material_id": "black_metal"},
    }
    for lid in ("leg_fl", "leg_fr", "leg_bl", "leg_br")
]


def drawer_ops(existing_materials: set[str]) -> list[dict[str, Any]]:
    ops: list[dict[str, Any]] = []
    deps: list[str] = []
    if "drawer_front" not in existing_materials:
        ops.append(
            {
                "operation_id": "op_dmat",
                "operation_type": "create_material",
                "parameters": {
                    "material_id": "drawer_front",
                    "name": "Drawer Front",
                    "base_color": "#E8E4DC",
                    "roughness": 0.5,
                },
            }
        )
        deps = ["op_dmat"]
    for i, (did, name, x) in enumerate([("drawer_left", "Drawer Left", -0.4), ("drawer_right", "Drawer Right", 0.4)]):
        ops.append(
            {
                "operation_id": f"op_drawer_{i}",
                "operation_type": "create_cube",
                "target_object_id": did,
                "dependencies": deps,
                "parameters": {
                    "name": name,
                    "dimensions": [0.45, 0.6, 0.12],
                    "location": [x, 0.0, 0.65],
                    "material_id": "drawer_front",
                    "parent_id": "desk_top",
                    "collection_id": "desk",
                },
                "metadata": {"component": "drawer"},
            }
        )
        ops.append(
            {
                "operation_id": f"op_handle_{i}",
                "operation_type": "create_cylinder",
                "target_object_id": f"{did}_handle",
                "dependencies": [f"op_drawer_{i}"],
                "parameters": {
                    "name": f"{name} Handle",
                    "radius": 0.008,
                    "depth": 0.12,
                    "location": [x, -0.31, 0.65],
                    "rotation": [0, 90, 0],
                    "material_id": "black_metal" if "black_metal" in existing_materials else None,
                    "parent_id": did,
                    "collection_id": "desk",
                },
                "metadata": {"component": "handle"},
            }
        )
    for op in ops:
        op["parameters"] = {k: v for k, v in op["parameters"].items() if v is not None}
    return ops


def _scene(messages: list[ChatMessage]) -> dict[str, Any]:
    """Pull the inventory JSON block the planner embeds in the user prompt."""
    for m in messages:
        match = re.search(r"<scene>\s*(\{.*?\})\s*</scene>", m.content, re.S)
        if match:
            return json.loads(match.group(1))
    return {"objects": [], "materials": []}


def _request(messages: list[ChatMessage]) -> str:
    for m in reversed(messages):
        match = re.search(r"<request>\s*(.*?)\s*</request>", m.content, re.S)
        if match:
            return match.group(1).lower()
    return messages[-1].content.lower()


class FakeProvider:
    name = "fake"
    model = "fake-planner"

    def __init__(self) -> None:
        self.calls: list[list[ChatMessage]] = []

    def _plan(self, messages: list[ChatMessage]) -> dict[str, Any]:
        text = _request(messages)
        scene = _scene(messages)
        ids = {o["id"] for o in scene.get("objects", [])}
        mats = {m["id"] for m in scene.get("materials", [])}

        if re.search(r"\b(export|download)\b", text):
            return {
                "intent": "export",
                "reply": "Your model is ready — use the download button to get the GLB.",
                "summary": "",
                "operations": [],
            }
        if "desk" in text and re.search(r"\b(create|make|build|design)\b", text) and "desk_top" not in ids:
            return {
                "intent": "modeling",
                "reply": "I created a modern oak desk with four slim graphite legs.",
                "summary": "Create a modern wooden desk",
                "operations": DESK_OPS,
            }
        if "leg" in text and "black" in text and {"leg_fl", "leg_fr", "leg_bl", "leg_br"} <= ids:
            return {
                "intent": "modeling",
                "reply": "The legs are now black metal; the top is unchanged.",
                "summary": "Make the legs black metal",
                "operations": BLACK_LEGS_OPS,
            }
        if "drawer" in text and "desk_top" in ids and "drawer_left" not in ids:
            return {
                "intent": "modeling",
                "reply": "I added two drawers under the desk top, each with a slim handle.",
                "summary": "Add two drawers",
                "operations": drawer_ops(mats),
            }
        if "walnut" in text and "desk_top" in ids:
            ops = [
                {
                    "operation_id": "op_walnut",
                    "operation_type": "create_material",
                    "parameters": {
                        "material_id": "walnut",
                        "name": "Walnut",
                        "base_color": "#5C3A21",
                        "roughness": 0.55,
                    },
                },
                {
                    "operation_id": "op_assign_top",
                    "operation_type": "assign_material",
                    "target_object_id": "desk_top",
                    "dependencies": ["op_walnut"],
                    "parameters": {"material_id": "walnut"},
                },
            ]
            if "walnut" in mats:
                ops = ops[1:]
                ops[0]["dependencies"] = []
            return {
                "intent": "modeling",
                "reply": "The desk top is now walnut; nothing else changed.",
                "summary": "Change the top to walnut",
                "operations": ops,
            }
        if re.search(r"\b(how many|what|which|list)\b", text):
            return {
                "intent": "question",
                "reply": f"The scene has {len(ids)} objects.",
                "summary": "",
                "operations": [],
            }
        return {
            "intent": "clarify",
            "reply": "Could you say a bit more about what you'd like to create or change?",
            "summary": "",
            "operations": [],
        }

    def generate_structured(self, messages, schema, *, max_tokens=None) -> Completion:  # noqa: ANN001
        self.calls.append(messages)
        return Completion(text=json.dumps(self._plan(messages)), model=self.model, prompt_tokens=0, completion_tokens=0)

    def generate_text(self, messages, *, max_tokens=None) -> Completion:  # noqa: ANN001
        return self.generate_structured(messages, {})


class ScriptedProvider:
    """Returns queued raw texts in order (tests for JSON repair / retry paths)."""

    name = "scripted"
    model = "scripted"

    def __init__(self, responses: list[str]) -> None:
        self.responses = list(responses)
        self.calls: list[list[ChatMessage]] = []

    def generate_structured(self, messages, schema, *, max_tokens=None) -> Completion:  # noqa: ANN001
        self.calls.append(messages)
        if not self.responses:
            raise AssertionError("ScriptedProvider ran out of responses")
        return Completion(text=self.responses.pop(0), model=self.model)

    def generate_text(self, messages, *, max_tokens=None) -> Completion:  # noqa: ANN001
        return self.generate_structured(messages, {})
