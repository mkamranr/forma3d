"""Compare two scene inventories (version compare, spec §6.5).

Objects and materials are matched by their stable application ids, so a rename is a change of
`name`, not a delete + create. Floats are compared with a small tolerance because Blender
round-trips transforms through float32.
"""

from __future__ import annotations

from typing import Any

from .schemas.scene_object import MaterialSnapshot, ObjectSnapshot, SceneInventory

TOL = 1e-4
OBJECT_FIELDS = (
    "name",
    "component_type",
    "parent_id",
    "location",
    "rotation",
    "dimensions",
    "visible",
    "material_ids",
    "modifiers",
    "vertices",
    "camera",
    "light",
)
MATERIAL_FIELDS = (
    "name",
    "base_color",
    "metallic",
    "roughness",
    "alpha",
    "emission_color",
    "emission_strength",
    "transmission",
    "ior",
    "texture",
)


def _same(a: Any, b: Any) -> bool:
    if isinstance(a, (int, float)) and isinstance(b, (int, float)) and not isinstance(a, bool):
        return abs(float(a) - float(b)) <= TOL
    if isinstance(a, (list, tuple)) and isinstance(b, (list, tuple)):
        return len(a) == len(b) and all(_same(x, y) for x, y in zip(a, b, strict=True))
    if isinstance(a, dict) and isinstance(b, dict):
        return a.keys() == b.keys() and all(_same(a[k], b[k]) for k in a)
    if isinstance(a, str) and isinstance(b, str) and a.startswith("#") and b.startswith("#"):
        return a.upper() == b.upper()
    return a == b


def _plain(v: Any) -> Any:
    if isinstance(v, tuple):
        return [_plain(x) for x in v]
    if isinstance(v, float):
        return round(v, 5)
    if isinstance(v, list):
        return [_plain(x) for x in v]
    return v


def _fields(before: dict[str, Any], after: dict[str, Any], names: tuple[str, ...]) -> dict[str, dict[str, Any]]:
    return {
        f: {"before": _plain(before.get(f)), "after": _plain(after.get(f))}
        for f in names
        if not _same(before.get(f), after.get(f))
    }


def _obj(o: ObjectSnapshot) -> dict[str, Any]:
    d = o.model_dump(mode="json")
    d["modifiers"] = sorted((m["type"], m["name"]) for m in d.get("modifiers", []))
    d["modifiers"] = [list(m) for m in d["modifiers"]]
    d["vertices"] = o.mesh.vertices  # geometry edits (extrude, booleans) that keep the bounding box
    return d


def _mat(m: MaterialSnapshot) -> dict[str, Any]:
    return m.model_dump(mode="json")


def diff_inventories(base: SceneInventory, target: SceneInventory) -> dict[str, Any]:
    """What changed going from `base` to `target`."""
    a = {o.object_id: _obj(o) for o in base.objects}
    b = {o.object_id: _obj(o) for o in target.objects}
    changed, unchanged = [], 0
    for oid in sorted(set(a) & set(b)):
        fields = _fields(a[oid], b[oid], OBJECT_FIELDS)
        if fields:
            changed.append({"object_id": oid, "name": b[oid]["name"], "fields": fields})
        else:
            unchanged += 1
    ma = {m.material_id: _mat(m) for m in base.materials}
    mb = {m.material_id: _mat(m) for m in target.materials}
    mat_changed = []
    for mid in sorted(set(ma) & set(mb)):
        fields = _fields(ma[mid], mb[mid], MATERIAL_FIELDS)
        if fields:
            mat_changed.append({"material_id": mid, "name": mb[mid]["name"], "fields": fields})

    def brief(d: dict[str, Any], key: str) -> dict[str, Any]:
        return {key: d[key], "name": d["name"]}

    return {
        "objects": {
            "added": [brief(b[k], "object_id") for k in sorted(set(b) - set(a))],
            "removed": [brief(a[k], "object_id") for k in sorted(set(a) - set(b))],
            "changed": changed,
            "unchanged": unchanged,
        },
        "materials": {
            "added": [brief(mb[k], "material_id") for k in sorted(set(mb) - set(ma))],
            "removed": [brief(ma[k], "material_id") for k in sorted(set(ma) - set(mb))],
            "changed": mat_changed,
        },
    }
