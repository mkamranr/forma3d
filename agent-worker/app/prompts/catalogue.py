"""Operation catalogue for the planner prompt, generated from the shared pydantic schemas so the
prompt can never drift from what the validator accepts (a test enforces a note per operation)."""

from __future__ import annotations

from functools import lru_cache
from typing import Any

from forma3d_shared.schemas.modeling_operation import OPERATION_MODELS

NOTES: dict[str, str] = {
    "create_cube": "box; origin at its centre; dimensions = full size [x,y,z]",
    "create_sphere": "UV sphere centred on location",
    "create_cylinder": "axis along local Z; origin at its centre; depth = height",
    "create_cone": "axis along local Z; origin at its centre; radius_top 0 = pointed",
    "create_plane": "flat in local XY; size = [x,y]",
    "create_torus": "ring in local XY plane",
    "move_object": "location = absolute world position, or offset = relative move (exactly one)",
    "rotate_object": "rotation = absolute local XYZ degrees, or delta (exactly one)",
    "scale_object": "object scale; prefer set_dimensions for real sizes",
    "set_dimensions": "resize to exact world size [x,y,z] in meters (keeps origin and children)",
    "duplicate_object": "copy with a NEW id (new_object_id), optional offset",
    "delete_object": "recursive=true also deletes children (required if it has children)",
    "rename_object": "display name only; the id never changes",
    "parent_object": "make target a child of parent_id (keeps world transform)",
    "unparent_object": "detach from parent (keeps world transform)",
    "set_visibility": "show/hide",
    "create_collection": "group for organisation",
    "move_to_collection": "move object into a collection",
    "create_material": "Principled material; base_color hex '#RRGGBB'",
    "assign_material": "replace the object's material",
    "update_material": "change an existing material (affects every object using it)",
    "set_base_color": "material colour",
    "set_metallic": "material metallic 0..1",
    "set_roughness": "material roughness 0..1",
    "set_transparency": "material alpha 0..1",
    "bevel": "round edges (modifier, mesh only); width in meters",
    "solidify": "give thickness to a surface (modifier, mesh only)",
}


def _type(schema: dict[str, Any]) -> str:
    if "anyOf" in schema:
        parts = [_type(s) for s in schema["anyOf"] if s.get("type") != "null"]
        return " | ".join(parts) or "null"
    if "enum" in schema:
        return "|".join(repr(v) for v in schema["enum"])
    t = schema.get("type")
    if t == "array" and "prefixItems" in schema:
        items = schema["prefixItems"]
        lo, hi = items[0].get("minimum"), items[0].get("maximum")
        rng = f" each {lo:g}..{hi:g}" if lo is not None and hi is not None else ""
        return (
            f"[{', '.join(_type({k: v for k, v in i.items() if k not in ('minimum', 'maximum')}) for i in items)}]{rng}"
        )
    if t == "array":
        return f"list[{_type(schema.get('items', {}))}]"
    if t in ("number", "integer"):
        lo, hi = schema.get("minimum"), schema.get("maximum")
        base = "int" if t == "integer" else "num"
        return f"{base} {lo:g}..{hi:g}" if lo is not None and hi is not None else base
    if t == "string":
        if schema.get("pattern") == "^#[0-9A-Fa-f]{6}$":
            return "hex"
        if schema.get("pattern", "").startswith("^[a-z][a-z0-9_]"):
            return "id"
        return "str"
    return t or "any"


def _op_type(model: type) -> str:
    return model.model_fields["operation_type"].annotation.__args__[0]  # type: ignore[attr-defined]


@lru_cache
def operation_catalogue() -> str:
    lines = []
    for model in OPERATION_MODELS:
        name = _op_type(model)
        needs_target = model.model_fields["target_object_id"].is_required()
        params_schema = model.model_fields["parameters"].annotation.model_json_schema()
        required = set(params_schema.get("required", []))
        parts = []
        for key, prop in params_schema.get("properties", {}).items():
            default = "" if key in required else f"={prop['default']!r}" if "default" in prop else "?"
            parts.append(f"{key}{'' if key in required else ''}: {_type(prop)}{default}")
        target = "target_object_id REQUIRED" if needs_target else "no target"
        lines.append(f"- {name} ({target}) — {NOTES.get(name, '')}\n    params: {'; '.join(parts) or 'none'}")
    return "\n".join(lines)


def operation_types() -> list[str]:
    return [_op_type(m) for m in OPERATION_MODELS]
