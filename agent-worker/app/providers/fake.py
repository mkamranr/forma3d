"""Deterministic provider for CI, demos and local development without an LLM server.

It plays the three LLM agents (recognised by the `# Role:` line of their system prompts) for the
spec scenarios: desk -> black metal legs -> two drawers -> walnut top -> export, an ambiguous
"make the drawer smaller" (clarification), questions, undo/restore. It reads the scene, task and QA
issues from the prompt like a real model, so every answer references objects that exist.

`flaws={"drawer_gap"}` makes the planner place drawers 10 cm too low, so tests can exercise the
QA -> Revision Agent loop against real geometry.
"""

from __future__ import annotations

import json
import re
from typing import Any

from .base import ChatMessage, Completion


def _block(messages: list[ChatMessage], tag: str) -> str:
    for m in reversed(messages):
        if m.role == "user" and (hit := re.search(rf"<{tag}>(.*?)</{tag}>", m.content, re.S)):
            return hit.group(1)
    return ""


def _json_block(messages: list[ChatMessage], tag: str, default: Any) -> Any:
    raw = _block(messages, tag)
    try:
        return json.loads(raw) if raw else default
    except ValueError:
        return default


def _role(messages: list[ChatMessage]) -> str:
    first = messages[0].content if messages else ""
    hit = re.match(r"# Role: (\w+)", first)
    return hit.group(1) if hit else "unknown"


def _selected(messages: list[ChatMessage]) -> str | None:
    hit = re.search(r"Selected object: (?:'[^']*' \(id )?([a-z][a-z0-9_]*)", messages[-1].content)
    return hit.group(1) if hit else None


LEGS = [
    ("leg_fl", "Leg Front Left", -0.75, -0.35),
    ("leg_fr", "Leg Front Right", 0.75, -0.35),
    ("leg_bl", "Leg Back Left", -0.75, 0.35),
    ("leg_br", "Leg Back Right", 0.75, 0.35),
]


def desk_plan() -> dict[str, Any]:
    parts: list[dict[str, Any]] = [
        {
            "id": "desk_top",
            "action": "create",
            "name": "Desk Top",
            "component": "tabletop",
            "shape": "box",
            "size": [1.6, 0.8, 0.04],
            "position": [0, 0, 0.74],
            "material": "oak",
            "bevel": 0.004,
        },
    ]
    for lid, name, x, y in LEGS:
        parts.append(
            {
                "id": lid,
                "action": "create",
                "name": name,
                "component": "leg",
                "shape": "box",
                "size": [0.05, 0.05, 0.72],
                "position": [x, y, 0.36],
                "material": "graphite",
                "parent": "desk_top",
            }
        )
    return {
        "summary": "Create a modern wooden desk",
        "reply": "I built a modern oak desk with four slim graphite legs.",
        "needs_clarification": False,
        "question": "",
        "materials": [
            {"id": "oak", "name": "Oak", "color": "#C99563", "roughness": 0.6},
            {"id": "graphite", "name": "Graphite", "color": "#424953", "metallic": 0.6, "roughness": 0.4},
        ],
        "parts": parts,
        "relations": [{"subject": lid, "relation": "under", "object": "desk_top"} for lid, *_ in LEGS],
    }


class FakeProvider:
    name = "fake"
    model = "fake-planner"

    def __init__(self, flaws: set[str] | None = None) -> None:
        self.flaws = flaws or set()
        self.calls: list[tuple[str, list[ChatMessage]]] = []

    # ------------------------------------------------------------------ conversation
    def _conversation(self, messages: list[ChatMessage]) -> dict[str, Any]:
        scene = _json_block(messages, "scene", {"objects": [], "materials": []})
        objects = scene.get("objects", [])
        request = _block(messages, "request").strip()
        clar = _block(messages, "clarification")
        original = re.search(r"The original request was: (.*)", clar)
        text = f"{original.group(1)} — {request}" if original else request
        low = text.lower()
        ids = [o["id"] for o in objects]
        drawers = sorted((o for o in objects if o.get("component") == "drawer"), key=lambda o: o["position"][0])
        base = {"task": "", "targets": [], "version_number": None, "reply": "", "question": ""}

        if re.search(r"\b(export|download)\b", low):
            return {**base, "intent": "export", "reply": "Your GLB is ready — use the download button."}
        if re.search(r"\bundo\b", low):
            return {**base, "intent": "undo", "reply": "I went back one version."}
        if m := re.search(r"\b(?:restore|back to|revert to)\b.*?\bversion (\d+)", low):
            n = int(m.group(1))
            return {**base, "intent": "restore", "version_number": n, "reply": f"Restored version {n}."}
        if re.match(r"\s*(how many|what|which|where|list)\b", low) or (low.endswith("?") and "make" not in low):
            names = ", ".join(o["name"] for o in objects) or "nothing yet"
            return {**base, "intent": "question", "reply": f"The scene has {len(objects)} objects: {names}."}
        if "drawer" in low and re.search(r"\b(smaller|bigger|larger|wider|narrower)\b", low):
            sel = _selected(messages)
            if sel in ids:
                targets = [sel]
            elif re.search(r"\bboth\b|\bdrawers\b", low):
                targets = [d["id"] for d in drawers]
            elif "left" in low and drawers:
                targets = [drawers[0]["id"]]
            elif "right" in low and drawers:
                targets = [drawers[-1]["id"]]
            elif len(drawers) == 1:
                targets = [drawers[0]["id"]]
            else:
                return {
                    **base,
                    "intent": "clarify",
                    "question": "There are two drawers — do you mean the left one, the right one, or both?",
                }
            verb = re.search(r"\b(smaller|bigger|larger|wider|narrower)\b", low).group(1)  # type: ignore[union-attr]
            return {
                **base,
                "intent": "modify",
                "targets": targets,
                "task": f"Make {', '.join(targets)} {verb}; nothing else changes.",
            }
        if not re.search(r"\b(create|make|build|add|change|set|turn|paint|move|delete|remove)\b", low):
            return {**base, "intent": "clarify", "question": "What would you like me to create or change?"}
        return {
            **base,
            "intent": "create" if not objects else "modify",
            "task": text,
            "targets": [i for i in ids if i in low],
        }

    # ------------------------------------------------------------------ planning
    def _planning(self, messages: list[ChatMessage]) -> dict[str, Any]:
        scene = _json_block(messages, "scene", {"objects": [], "materials": []})
        objects = {o["id"]: o for o in scene.get("objects", [])}
        mats = {m["id"] for m in scene.get("materials", [])}
        task = _block(messages, "task").lower()
        empty = {
            "summary": "",
            "reply": "",
            "needs_clarification": False,
            "question": "",
            "materials": [],
            "parts": [],
            "relations": [],
        }

        if not objects and re.search(r"\b(desk|table)\b", task):
            return desk_plan()
        if "leg" in task and "black" in task:
            legs = [i for i, o in objects.items() if o.get("component") == "leg"]
            return {
                **empty,
                "summary": "Make the legs black metal",
                "reply": "The legs are now black metal; the top is unchanged.",
                "materials": []
                if "black_metal" in mats
                else [
                    {"id": "black_metal", "name": "Black Metal", "color": "#111111", "metallic": 1.0, "roughness": 0.35}
                ],
                "parts": [{"id": i, "action": "modify", "material": "black_metal"} for i in legs],
            }
        if "drawer" in task and re.search(r"\b(add|two|2)\b", task) and "desk_top" in objects:
            top_min_z = objects["desk_top"]["bounds"]["min"][2]
            z = round(top_min_z - 0.06 - (0.10 if "drawer_gap" in self.flaws else 0.0), 4)
            parts, relations = [], []
            for did, name, x in [("drawer_left", "Drawer Left", -0.4), ("drawer_right", "Drawer Right", 0.4)]:
                parts.append(
                    {
                        "id": did,
                        "action": "create",
                        "name": name,
                        "component": "drawer",
                        "shape": "box",
                        "size": [0.5, 0.55, 0.12],
                        "position": [x, 0.0, z],
                        "material": "drawer_front",
                        "parent": "desk_top",
                    }
                )
                parts.append(
                    {
                        "id": f"{did}_handle",
                        "action": "create",
                        "name": f"{name} Handle",
                        "component": "handle",
                        "shape": "box",
                        "size": [0.12, 0.02, 0.02],
                        "position": [x, -0.285, z],
                        "material": "black_metal" if "black_metal" in mats else "drawer_front",
                        "parent": did,
                    }
                )
                relations += [
                    {"subject": did, "relation": "under", "object": "desk_top"},
                    {"subject": f"{did}_handle", "relation": "attached_to", "object": did},
                ]
            return {
                **empty,
                "summary": "Add two drawers",
                "reply": "I added two drawers under the desk top, each with a slim handle.",
                "materials": []
                if "drawer_front" in mats
                else [{"id": "drawer_front", "name": "Drawer Front", "color": "#E8E4DC", "roughness": 0.5}],
                "parts": parts,
                "relations": relations,
            }
        if "walnut" in task and "desk_top" in objects:
            return {
                **empty,
                "summary": "Change the top to walnut",
                "reply": "The desk top is now walnut; nothing else changed.",
                "materials": []
                if "walnut" in mats
                else [{"id": "walnut", "name": "Walnut", "color": "#5C3A21", "roughness": 0.55}],
                "parts": [{"id": "desk_top", "action": "modify", "material": "walnut"}],
            }
        if re.search(r"\b(smaller|bigger|larger|wider|narrower)\b", task):
            targets = [i for i in objects if re.search(rf"\b{i}\b", task)]
            factor = 0.8 if re.search(r"\b(smaller|narrower)\b", task) else 1.2
            parts = [
                {
                    "id": i,
                    "action": "modify",
                    "size": [round(objects[i]["size"][0] * factor, 4), *objects[i]["size"][1:]],
                }
                for i in targets
            ]
            if parts:
                return {
                    **empty,
                    "summary": f"Resize {', '.join(targets)}",
                    "reply": f"I made {', '.join(targets)} {'smaller' if factor < 1 else 'bigger'}.",
                    "parts": parts,
                }
        return {
            **empty,
            "needs_clarification": True,
            "question": "I'm not sure how to build that yet — could you describe it in more detail?",
        }

    # ------------------------------------------------------------------ revision
    def _revision(self, messages: list[ChatMessage]) -> dict[str, Any]:
        issues = _json_block(messages, "issues", [])
        parts: dict[str, dict[str, Any]] = {}
        for issue in issues:
            pos = (issue.get("data") or {}).get("suggested_position")
            if issue.get("object_id") and pos:
                parts[issue["object_id"]] = {"id": issue["object_id"], "action": "modify", "position": pos}
        return {
            "summary": "Fix placement",
            "reply": "I added two drawers flush under the desk top, each with a handle.",
            "materials": [],
            "parts": list(parts.values()),
            "relations": [],
        }

    # ------------------------------------------------------------------ protocol
    def generate_structured(self, messages, schema, *, max_tokens=None) -> Completion:  # noqa: ANN001
        role = _role(messages)
        self.calls.append((role, messages))
        handler = {"conversation": self._conversation, "planning": self._planning, "revision": self._revision}.get(role)
        answer = handler(messages) if handler else {}
        return Completion(text=json.dumps(answer), model=self.model)

    def generate_text(self, messages, *, max_tokens=None) -> Completion:  # noqa: ANN001
        return self.generate_structured(messages, {})


class ScriptedProvider:
    """Returns canned responses in order (tests for repair loops and failure handling)."""

    name = "scripted"
    model = "scripted"

    def __init__(self, responses: list[str]) -> None:
        self.responses = list(responses)
        self.calls: list[list[ChatMessage]] = []

    def generate_structured(self, messages, schema, *, max_tokens=None) -> Completion:  # noqa: ANN001
        self.calls.append(messages)
        return Completion(text=self.responses.pop(0) if self.responses else "{}", model=self.model)

    def generate_text(self, messages, *, max_tokens=None) -> Completion:  # noqa: ANN001
        return self.generate_structured(messages, {})
