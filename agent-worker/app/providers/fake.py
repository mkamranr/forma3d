"""Deterministic provider for CI, demos and local development without an LLM server.

It plays the three LLM agents (recognised by the `# Role:` line of their system prompts) for the
spec scenarios: desk -> black metal legs -> two drawers -> walnut top -> export, an ambiguous
"make the drawer smaller" (clarification), questions, undo/restore. It reads the scene, task and QA
issues from the prompt like a real model, so every answer references objects that exist.

Image-to-3D: the Vision Agent's role is answered from the words sent with the photo (no pixels are read):
"chair" -> an office chair, "cabinet"/"wardrobe" -> a storage cabinet, "blank" -> no object, anything
else -> a desk. The description then flows through the normal conversation/planning path.

Phase 7 scenarios: storage cabinet (wood texture, doors, handles), office chair (five-star base),
a cable hole cut into the desk top (boolean), glass/texture looks, lights, exports and renders.

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


def _selected(messages: list[ChatMessage]) -> list[str]:
    """Ids of the selected objects (primary last), from the "Selected object(s): ..." line."""
    hit = re.search(r"Selected objects?: (.*)", messages[-1].content)
    return re.findall(r"\(id ([a-z][a-z0-9_]*)\)", hit.group(1)) if hit else []


COLORS = {
    "red": "#B3261E",
    "blue": "#1F4E99",
    "dark blue": "#1B2A4A",
    "green": "#2E7D32",
    "black": "#111111",
    "white": "#F2F2F2",
    "grey": "#808080",
    "gray": "#808080",
    "yellow": "#E0B422",
    "orange": "#D9711F",
}


def _color(text: str) -> tuple[str, str] | None:
    for name in sorted(COLORS, key=len, reverse=True):
        if re.search(rf"\b{name}\b", text):
            return name, COLORS[name]
    return None


def chair_plan() -> dict[str, Any]:
    seat_top = 0.475
    parts: list[dict[str, Any]] = [
        {
            "id": "seat",
            "action": "create",
            "name": "Seat",
            "component": "seat",
            "shape": "box",
            "size": [0.45, 0.45, 0.05],
            "position": [0, 0, 0.45],
            "material": "beech",
            "bevel": 0.004,
        },
        {
            "id": "backrest",
            "action": "create",
            "name": "Backrest",
            "component": "backrest",
            "shape": "box",
            "size": [0.45, 0.04, 0.45],
            "position": [0, 0.205, seat_top + 0.225],
            "material": "beech",
            "parent": "seat",
        },
    ]
    legs = [
        ("leg_fl", "Leg Front Left", -0.19, -0.19),
        ("leg_fr", "Leg Front Right", 0.19, -0.19),
        ("leg_bl", "Leg Back Left", -0.19, 0.19),
        ("leg_br", "Leg Back Right", 0.19, 0.19),
    ]
    for lid, name, x, y in legs:
        parts.append(
            {
                "id": lid,
                "action": "create",
                "name": name,
                "component": "leg",
                "shape": "box",
                "size": [0.04, 0.04, 0.425],
                "position": [x, y, 0.2125],
                "material": "steel",
                "parent": "seat",
            }
        )
    return {
        "summary": "Create a chair",
        "reply": "I built a simple chair: a beech seat and backrest on four steel legs.",
        "needs_clarification": False,
        "question": "",
        "materials": [
            {"id": "beech", "name": "Beech", "color": "#C8A27A", "roughness": 0.6},
            {"id": "steel", "name": "Steel", "color": "#9AA1A9", "metallic": 0.9, "roughness": 0.3},
        ],
        "parts": parts,
        "relations": [{"subject": "backrest", "relation": "on_top_of", "object": "seat"}]
        + [{"subject": lid, "relation": "under", "object": "seat"} for lid, *_ in legs],
    }


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


def cabinet_plan() -> dict[str, Any]:
    """Storage cabinet: wood-textured carcass, two doors with bar handles, an inner shelf."""
    parts: list[dict[str, Any]] = [
        {
            "id": "cabinet_body",
            "action": "create",
            "name": "Cabinet Body",
            "component": "carcass",
            "shape": "box",
            "size": [0.9, 0.45, 1.8],
            "position": [0, 0, 0.9],
            "material": "cabinet_oak",
            "bevel": 0.003,
        },
    ]
    relations: list[dict[str, str]] = []
    for did, name, x in (("door_left", "Door Left", -0.2245), ("door_right", "Door Right", 0.2245)):
        parts.append(
            {
                "id": did,
                "action": "create",
                "name": name,
                "component": "door",
                "shape": "box",
                "size": [0.445, 0.02, 1.76],
                "position": [x, -0.235, 0.9],
                "material": "cabinet_oak",
                "parent": "cabinet_body",
            }
        )
        hx = round(x + (0.17 if x < 0 else -0.17), 4)
        parts.append(
            {
                "id": f"{did}_handle",
                "action": "create",
                "name": f"{name} Handle",
                "component": "handle",
                "shape": "cylinder",
                "size": [0.016, 0.016, 0.22],
                "position": [hx, -0.253, 1.0],
                "material": "brushed_steel",
                "parent": did,
            }
        )
        relations += [
            {"subject": did, "relation": "attached_to", "object": "cabinet_body"},
            {"subject": f"{did}_handle", "relation": "attached_to", "object": did},
        ]
    return {
        "summary": "Create a storage cabinet",
        "reply": "I built an oak storage cabinet with two doors and brushed-steel bar handles.",
        "needs_clarification": False,
        "question": "",
        "materials": [
            {"id": "cabinet_oak", "name": "Cabinet Oak", "color": "#B98A5A", "roughness": 0.55, "texture": "wood"},
            {
                "id": "brushed_steel",
                "name": "Brushed Steel",
                "color": "#B8BCC2",
                "metallic": 1.0,
                "roughness": 0.35,
                "texture": "brushed_metal",
            },
        ],
        "parts": parts,
        "relations": relations,
    }


def office_chair_plan() -> dict[str, Any]:
    """Office chair: five-star base with casters, gas column, padded seat and backrest."""
    import math

    parts: list[dict[str, Any]] = [
        {
            "id": "chair_hub",
            "action": "create",
            "name": "Base Hub",
            "component": "base",
            "shape": "cylinder",
            "size": [0.08, 0.08, 0.06],
            "position": [0, 0, 0.08],
            "material": "chrome",
        },
        {
            "id": "gas_column",
            "action": "create",
            "name": "Gas Column",
            "component": "column",
            "shape": "cylinder",
            "size": [0.05, 0.05, 0.3],
            "position": [0, 0, 0.26],
            "material": "chrome",
            "parent": "chair_hub",
        },
        {
            "id": "seat",
            "action": "create",
            "name": "Seat",
            "component": "seat",
            "shape": "box",
            "size": [0.5, 0.48, 0.08],
            "position": [0, 0, 0.45],
            "material": "mesh_fabric",
            "parent": "gas_column",
            "bevel": 0.02,
        },
        {
            "id": "backrest",
            "action": "create",
            "name": "Backrest",
            "component": "backrest",
            "shape": "box",
            "size": [0.46, 0.06, 0.55],
            "position": [0, 0.21, 0.765],
            "material": "mesh_fabric",
            "parent": "seat",
            "bevel": 0.02,
        },
    ]
    relations = [
        {"subject": "gas_column", "relation": "on_top_of", "object": "chair_hub"},
        {"subject": "seat", "relation": "on_top_of", "object": "gas_column"},
        {"subject": "backrest", "relation": "on_top_of", "object": "seat"},
    ]
    for k in range(5):
        a = math.radians(90 + 72 * k)
        lid, cid = f"star_leg_{k + 1}", f"caster_{k + 1}"
        parts.append(
            {
                "id": lid,
                "action": "create",
                "name": f"Star Leg {k + 1}",
                "component": "leg",
                "shape": "box",
                "size": [0.3, 0.04, 0.03],
                "position": [round(0.17 * math.cos(a), 4), round(0.17 * math.sin(a), 4), 0.07],
                "rotation": [0, 0, round(90 + 72 * k, 3)],
                "material": "chrome",
                "parent": "chair_hub",
            }
        )
        parts.append(
            {
                "id": cid,
                "action": "create",
                "name": f"Caster {k + 1}",
                "component": "caster",
                "shape": "sphere",
                "size": [0.06, 0.06, 0.06],
                "position": [round(0.3 * math.cos(a), 4), round(0.3 * math.sin(a), 4), 0.03],
                "material": "black_plastic",
                "parent": lid,
            }
        )
        relations += [
            {"subject": lid, "relation": "attached_to", "object": "chair_hub"},
            {"subject": cid, "relation": "attached_to", "object": lid},
        ]
    return {
        "summary": "Create an office chair",
        "reply": "I built an office chair: five-star chrome base on casters, gas column, padded seat and backrest.",
        "needs_clarification": False,
        "question": "",
        "materials": [
            {"id": "mesh_fabric", "name": "Mesh Fabric", "color": "#2B2F36", "roughness": 0.85, "texture": "fabric"},
            {"id": "chrome", "name": "Chrome", "color": "#D7DADF", "metallic": 1.0, "roughness": 0.15},
            {"id": "black_plastic", "name": "Black Plastic", "color": "#151515", "roughness": 0.5},
        ],
        "parts": parts,
        "relations": relations,
    }


EXPORT_RE = re.compile(r"\b(stl|obj|gltf|glb)\b")


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
        reference = _block(messages, "reference_image").strip()
        if reference:  # a described photo: build what it shows
            request = f"{request} {reference.splitlines()[0]}"
        clar = _block(messages, "clarification")
        original = re.search(r"The original request was: (.*)", clar)
        text = f"{original.group(1)} — {request}" if original else request
        low = text.lower()
        ids = [o["id"] for o in objects]
        drawers = sorted((o for o in objects if o.get("component") == "drawer"), key=lambda o: o["position"][0])
        base = {"task": "", "targets": [], "version_number": None, "reply": "", "question": ""}

        if re.search(r"\b(export|download)\b", low) or (objects and EXPORT_RE.search(low) and "as" in low):
            fmt = (EXPORT_RE.search(low) or re.match("(glb)", "glb")).group(1)  # type: ignore[union-attr]
            return {
                **base,
                "intent": "export",
                "export_format": fmt,
                "reply": f"Preparing your {fmt.upper()} file — it will appear here to download.",
            }
        if objects and re.search(r"\b(render|picture|photo|image)\b", low) and not re.search(r"\b(add|camera)\b", low):
            quality = (
                "high"
                if re.search(r"high.quality|final|photo.?real", low)
                else "draft"
                if "quick" in low
                else "standard"
            )
            return {**base, "intent": "render", "render_quality": quality, "reply": "Rendering your model now."}
        if re.search(r"\bredo\b", low):
            return {**base, "intent": "redo", "reply": "I re-applied the change."}
        if re.search(r"\bundo\b|\bprevious version\b|\bgo back\b(?!.*version \d)", low):
            return {**base, "intent": "undo", "reply": "I went back one version."}
        if m := re.search(r"\b(?:restore|back to|revert to)\b.*?\bversion (\d+)", low):
            n = int(m.group(1))
            return {**base, "intent": "restore", "version_number": n, "reply": f"Restored version {n}."}
        if re.match(r"\s*(how many|what|which|where|list)\b", low) or (low.endswith("?") and "make" not in low):
            names = ", ".join(o["name"] for o in objects) or "nothing yet"
            return {**base, "intent": "question", "reply": f"The scene has {len(objects)} objects: {names}."}
        selected = [s for s in _selected(messages) if s in ids]
        if selected and re.search(r"\b(it|its|this|these|them|selected|selection)\b", low):
            return {
                **base,
                "intent": "modify",
                "targets": selected,
                "task": f"{text} (applies to {', '.join(selected)} only; nothing else changes)",
            }
        if "drawer" in low and re.search(r"\b(smaller|bigger|larger|wider|narrower)\b", low):
            if selected:
                targets = selected[-1:]
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
        if not re.search(r"\b(create|make|build|add|change|set|turn|paint|move|delete|remove|cut|drill|give)\b", low):
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
        targets = [i for i in objects if re.search(rf"\b{i}\b", task)]
        if not objects and "cabinet" in task:
            return cabinet_plan()
        if not objects and "office chair" in task:
            return office_chair_plan()
        if not objects and "chair" in task:
            return chair_plan()
        if re.search(r"\b(hole|grommet)\b", task) and "desk_top" in objects:
            top = objects["desk_top"]
            x = round(top["position"][0] + top["size"][0] / 2 - 0.15, 4)
            y = round(top["position"][1] + top["size"][1] / 2 - 0.1, 4)
            return {
                **empty,
                "summary": "Cut a cable hole in the desk top",
                "reply": "I cut a 6 cm cable hole in the back right corner of the desk top.",
                "parts": [
                    {
                        "id": "cable_hole_cutter",
                        "action": "create",
                        "name": "Cable Hole",
                        "shape": "cylinder",
                        "size": [0.06, 0.06, round(top["size"][2] + 0.02, 4)],
                        "position": [x, y, top["position"][2]],
                        "cut_into": "desk_top",
                    }
                ],
            }
        if re.search(r"\b(light|lamp)\b", task) and objects and re.search(r"\b(add|light up)\b", task):
            tops = [o["bounds"]["max"][2] for o in objects.values() if "bounds" in o]
            warm = "warm" in task
            return {
                **empty,
                "summary": "Add a light",
                "reply": "I added a soft area light above the model." + (" It is a warm white." if warm else ""),
                "parts": [
                    {
                        "id": "key_light",
                        "action": "create",
                        "name": "Key Light",
                        "shape": "light",
                        "light_type": "area",
                        "position": [0.6, -0.8, round(max(tops, default=1.0) + 1.2, 3)],
                        "energy": 400,
                        "color": "#FFD9A8" if warm else "#FFFFFF",
                    }
                ],
            }
        if "glass" in task and targets:
            return {
                **empty,
                "summary": f"Make {', '.join(targets)} glass",
                "reply": f"{', '.join(targets)} is now clear glass.",
                "materials": []
                if "clear_glass" in mats
                else [
                    {
                        "id": "clear_glass",
                        "name": "Clear Glass",
                        "color": "#EAF4F7",
                        "roughness": 0.05,
                        "transmission": 1.0,
                    }
                ],
                "parts": [{"id": i, "action": "modify", "material": "clear_glass"} for i in targets],
            }
        if (
            (tex := re.search(r"\b(wood|marble|brick|fabric|concrete|checker)\b", task))
            and "texture" in task
            and targets
        ):
            kind = tex.group(1)
            used = {o.get("material") for i, o in objects.items() if i in targets} - {None}
            return {
                **empty,
                "summary": f"Give {', '.join(targets)} a {kind} texture",
                "reply": f"{', '.join(targets)} now has a {kind} texture.",
                "materials": [{"id": m, "texture": kind} for m in sorted(used)],
                "parts": [],
            }
        targets = [i for i in objects if re.search(rf"\b{i}\b", task)]
        if "taller" in task and targets:
            parts = []
            for i in targets:
                o = objects[i]
                h = round(o["size"][2] * 1.25, 4)
                bottom = o["bounds"]["min"][2]
                parts.append(
                    {
                        "id": i,
                        "action": "modify",
                        "size": [*o["size"][:2], h],
                        "position": [*o["position"][:2], round(bottom + h / 2, 4)],
                    }
                )
            return {
                **empty,
                "summary": f"Make {', '.join(targets)} taller",
                "reply": f"I made {', '.join(targets)} 25% taller; its base stays where it was.",
                "parts": parts,
            }
        if (col := _color(task)) and targets and re.search(r"\b(colou?r|paint|make it|turn)\b", task):
            name, hexcode = col
            mid = name.replace(" ", "_") + "_paint"
            return {
                **empty,
                "summary": f"Paint {', '.join(targets)} {name}",
                "reply": f"I painted {', '.join(targets)} {name}; parts sharing its old material are unchanged.",
                "materials": []
                if mid in mats
                else [{"id": mid, "name": f"{name.title()} Paint", "color": hexcode, "roughness": 0.5}],
                "parts": [{"id": i, "action": "modify", "material": mid} for i in targets],
            }
        if "desk" in task and re.search(r"\b(wider|longer)\b", task) and "desk_top" in objects:
            cm = re.search(r"(\d+)\s*cm", task)
            grow = int(cm.group(1)) / 100 if cm else 0.2
            top = objects["desk_top"]
            parts = [
                {"id": "desk_top", "action": "modify", "size": [round(top["size"][0] + grow, 4), *top["size"][1:]]}
            ]
            for i, o in objects.items():
                if o.get("component") == "leg":
                    x = o["position"][0]
                    parts.append(
                        {
                            "id": i,
                            "action": "modify",
                            "position": [round(x + (grow / 2 if x > 0 else -grow / 2), 4), *o["position"][1:]],
                        }
                    )
            return {
                **empty,
                "summary": f"Make the desk {round(grow * 100)} cm wider",
                "reply": f"The desk is now {round(grow * 100)} cm wider; the legs moved out to the new corners.",
                "parts": parts,
            }
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

    # ------------------------------------------------------------------ vision
    def _vision(self, messages: list[ChatMessage]) -> dict[str, Any]:
        user = messages[-1] if messages else ChatMessage("user", "")
        hint = user.content.lower()
        self.images_seen = len(user.images)
        if "blank" in hint:
            return {"is_object": False, "object": "", "summary": "an empty white image", "parts": [], "materials": []}
        if "chair" in hint:
            obj, size, parts = "office chair", (0.65, 0.65, 1.1), [("seat", "box"), ("backrest", "box")]
        elif "cabinet" in hint or "wardrobe" in hint:
            obj, size, parts = "storage cabinet", (0.9, 0.45, 1.8), [("body", "box"), ("door", "box")]
        else:
            obj, size, parts = "modern wooden desk", (1.6, 0.8, 0.75), [("desk top", "box"), ("leg", "box")]
        return {
            "is_object": True,
            "object": obj,
            "summary": f"A simple {obj}.",
            "style": "modern",
            "overall_size_m": dict(zip(("width", "depth", "height"), size, strict=True)),
            "parts": [
                {
                    "name": n,
                    "shape": sh,
                    "count": 1,
                    "size_m": {"width": 0.5, "depth": 0.5, "height": 0.05},
                    "position": "centred",
                    "material": "oak",
                }
                for n, sh in parts
            ],
            "materials": [{"name": "oak", "color": "#C99563", "finish": "satin", "texture": "wood"}],
            "notes": "",
            "confidence": "high",
        }

    # ------------------------------------------------------------------ protocol
    def generate_structured(self, messages, schema, *, max_tokens=None) -> Completion:  # noqa: ANN001
        role = _role(messages)
        self.calls.append((role, messages))
        handler = {
            "conversation": self._conversation,
            "planning": self._planning,
            "revision": self._revision,
            "vision": self._vision,
        }.get(role)
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
