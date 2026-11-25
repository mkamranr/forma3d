"""The component plan: what the Planning and Revision agents produce.

It describes *design* decisions (parts, sizes, world positions, materials, hierarchy, intended
relationships) and leaves operation mechanics (ids, ordering, dependencies, primitive parameters)
to the deterministic Modeling Agent. Everything here is untrusted LLM output: the compiled DSL is
still validated by the shared validator before Blender sees it.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

ID = r"^[a-z][a-z0-9_]{0,63}$"
Shape = Literal["box", "cylinder", "sphere", "cone", "torus", "plane", "light"]
SHAPES = ["box", "cylinder", "sphere", "cone", "torus", "plane", "light"]
Texture = Literal["wood", "marble", "noise", "checker", "brick", "fabric", "concrete", "brushed_metal", ""]
TEXTURES = ["wood", "marble", "noise", "checker", "brick", "fabric", "concrete", "brushed_metal", ""]
LightType = Literal["point", "sun", "spot", "area"]
Relation = Literal["on_top_of", "attached_to", "inside", "under", "beside"]


class _Loose(BaseModel):
    model_config = ConfigDict(extra="ignore")


def _vec(v: Any) -> Any:
    if v is None:
        return v
    if isinstance(v, (list, tuple)) and len(v) == 3:
        return [float(x) for x in v]
    raise ValueError("must be a list of 3 numbers")


class PartSpec(_Loose):
    id: str = Field(pattern=ID)
    action: Literal["create", "modify", "delete"]
    name: str | None = Field(default=None, max_length=64)
    component: str | None = Field(default=None, pattern=ID)
    shape: Shape | None = None
    size: list[float] | None = None  # full extents X, Y, Z in meters (local axes)
    position: list[float] | None = None  # world-space centre
    rotation: list[float] | None = None  # degrees, XYZ Euler
    material: str | None = Field(default=None, pattern=ID)
    parent: str | None = None  # object id, or "" to detach
    visible: bool | None = None
    bevel: float | None = Field(default=None, ge=0, le=0.5)
    #: this part is a cutting tool: it is subtracted from that object (a hole, slot or groove) and removed
    cut_into: str | None = Field(default=None, pattern=ID)
    # shape "light"
    light_type: LightType | None = None
    energy: float | None = Field(default=None, ge=0, le=100000)
    color: str | None = Field(default=None, pattern=r"^#[0-9A-Fa-f]{6}$")

    _v = field_validator("size", "position", "rotation", mode="before")(_vec)

    @field_validator("parent")
    @classmethod
    def _parent(cls, v: str | None) -> str | None:
        if v in (None, ""):
            return v
        import re

        if not re.match(ID, v):
            raise ValueError("parent must be an object id")
        return v


class MaterialSpec(_Loose):
    id: str = Field(pattern=ID)
    name: str | None = Field(default=None, max_length=64)
    color: str | None = Field(default=None, pattern=r"^#[0-9A-Fa-f]{6}$")
    metallic: float | None = Field(default=None, ge=0, le=1)
    roughness: float | None = Field(default=None, ge=0, le=1)
    alpha: float | None = Field(default=None, ge=0, le=1)
    texture: Texture | None = None  # procedural colour pattern; "" removes it
    emission: float | None = Field(default=None, ge=0, le=50)  # glow strength in the material colour
    transmission: float | None = Field(default=None, ge=0, le=1)  # 1 = clear glass


class RelationSpec(_Loose):
    subject: str = Field(pattern=ID)
    relation: Relation
    object: str = Field(pattern=ID)


class Plan(_Loose):
    summary: str = Field(default="", max_length=300)
    reply: str = Field(default="", max_length=1500)
    needs_clarification: bool = False
    question: str = Field(default="", max_length=500)
    materials: list[MaterialSpec] = Field(default_factory=list, max_length=50)
    parts: list[PartSpec] = Field(default_factory=list, max_length=200)
    relations: list[RelationSpec] = Field(default_factory=list, max_length=200)


def plan_json_schema(*, with_clarification: bool = True) -> dict[str, Any]:
    """Structured-output schema (kept flat and small: guided decoding cost grows with schema size)."""
    vec = {"type": "array", "items": {"type": "number"}, "minItems": 3, "maxItems": 3}
    props: dict[str, Any] = {
        "summary": {"type": "string", "maxLength": 300},
        "reply": {"type": "string", "maxLength": 1500},
        "materials": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["id"],
                "properties": {
                    "id": {"type": "string"},
                    "name": {"type": "string"},
                    "color": {"type": "string"},
                    "metallic": {"type": "number"},
                    "roughness": {"type": "number"},
                    "alpha": {"type": "number"},
                    "texture": {"type": "string", "enum": TEXTURES},
                    "emission": {"type": "number"},
                    "transmission": {"type": "number"},
                },
            },
        },
        "parts": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["id", "action"],
                "properties": {
                    "id": {"type": "string"},
                    "action": {"type": "string", "enum": ["create", "modify", "delete"]},
                    "name": {"type": "string"},
                    "component": {"type": "string"},
                    "shape": {"type": "string", "enum": SHAPES},
                    "size": vec,
                    "position": vec,
                    "rotation": vec,
                    "material": {"type": "string"},
                    "parent": {"type": "string"},
                    "visible": {"type": "boolean"},
                    "bevel": {"type": "number"},
                    "cut_into": {"type": "string"},
                    "light_type": {"type": "string", "enum": ["point", "sun", "spot", "area"]},
                    "energy": {"type": "number"},
                    "color": {"type": "string"},
                },
            },
        },
        "relations": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["subject", "relation", "object"],
                "properties": {
                    "subject": {"type": "string"},
                    "relation": {"type": "string", "enum": ["on_top_of", "attached_to", "inside", "under", "beside"]},
                    "object": {"type": "string"},
                },
            },
        },
    }
    required = ["summary", "reply", "parts"]
    if with_clarification:
        props["needs_clarification"] = {"type": "boolean"}
        props["question"] = {"type": "string", "maxLength": 500}
        required.append("needs_clarification")
    return {"type": "object", "required": required, "properties": props}


def merge_revision(plan: Plan, revision: Plan) -> Plan:
    """Apply a Revision Agent patch to the current plan. Revisions name parts by id; fields they set
    override the plan's, everything else is kept (minimal changes, no rebuild of unaffected parts)."""
    parts = {p.id: p for p in plan.parts}
    order = [p.id for p in plan.parts]
    moved: dict[str, list[float]] = {}
    for patch in revision.parts:
        base = parts.get(patch.id)
        if patch.action == "delete":
            if base is not None and base.action == "create":
                parts.pop(patch.id)  # never build it in the first place
                order.remove(patch.id)
            else:
                parts[patch.id] = patch
                if patch.id not in order:
                    order.append(patch.id)
            continue
        if base is None:
            parts[patch.id] = patch
            order.append(patch.id)
            continue
        updates = {k: v for k, v in patch.model_dump(exclude_unset=True).items() if k not in ("id", "action")}
        parts[patch.id] = base.model_copy(update=updates)
        if base.action == "create" and "position" in updates and base.position and patch.position:
            moved[patch.id] = [patch.position[i] - base.position[i] for i in range(3)]
    # Planned children of a moved planned part follow it (as they would once parented in Blender),
    # unless the revision placed them explicitly.
    explicit = {p.id for p in revision.parts if "position" in p.model_fields_set}
    for pid in order:
        p = parts.get(pid)
        if p is None or p.action != "create" or pid in explicit or not p.position:
            continue
        anc, delta = p.parent, None
        while anc and delta is None:
            delta = moved.get(anc)
            anc = parts[anc].parent if anc in parts else None
        if delta:
            parts[pid] = p.model_copy(update={"position": [round(p.position[i] + delta[i], 6) for i in range(3)]})
    materials = {m.id: m for m in plan.materials}
    for m in revision.materials:
        base_m = materials.get(m.id)
        materials[m.id] = base_m.model_copy(update=m.model_dump(exclude_unset=True)) if base_m else m
    relations = list(plan.relations)
    for r in revision.relations:
        if r not in relations:
            relations.append(r)
    return plan.model_copy(
        update={
            "parts": [parts[i] for i in order if i in parts],
            "materials": list(materials.values()),
            "relations": relations,
        }
    )
