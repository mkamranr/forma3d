"""Scene Inspection Agent (deterministic).

The inventory it reads is produced inside Blender by `bl_runtime`'s scene inspector at the end of
every job (objects, transforms, dimensions, materials, hierarchy, modifiers, mesh stats), so this
agent never needs an LLM: it derives world-space bounds and a compact report for the other agents.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

from forma3d_shared.schemas.scene_object import ObjectSnapshot, SceneInventory

Vec3 = tuple[float, float, float]


def rotation_matrix(deg: Vec3) -> list[list[float]]:
    """Blender XYZ Euler (degrees): R = Rz · Ry · Rx."""
    x, y, z = (math.radians(a) for a in deg)
    cx, sx, cy, sy, cz, sz = math.cos(x), math.sin(x), math.cos(y), math.sin(y), math.cos(z), math.sin(z)
    return [
        [cy * cz, sx * sy * cz - cx * sz, cx * sy * cz + sx * sz],
        [cy * sz, sx * sy * sz + cx * cz, cx * sy * sz - sx * cz],
        [-sy, sx * cy, cx * cy],
    ]


@dataclass(frozen=True)
class Box:
    min: Vec3
    max: Vec3

    @property
    def size(self) -> Vec3:
        return (self.max[0] - self.min[0], self.max[1] - self.min[1], self.max[2] - self.min[2])

    def gap(self, other: Box) -> float:
        """Euclidean distance between two boxes (0 when they touch or overlap)."""
        d = [max(0.0, other.min[i] - self.max[i], self.min[i] - other.max[i]) for i in range(3)]
        return math.sqrt(sum(v * v for v in d))

    def overlaps_xy(self, other: Box, tol: float = 0.0) -> bool:
        return all(self.min[i] <= other.max[i] + tol and other.min[i] <= self.max[i] + tol for i in (0, 1))

    def contains(self, other: Box, tol: float) -> bool:
        return all(other.min[i] >= self.min[i] - tol and other.max[i] <= self.max[i] + tol for i in range(3))


def world_box(location: Vec3, dimensions: Vec3, rotation: Vec3 = (0.0, 0.0, 0.0)) -> Box:
    """Axis-aligned world bounds of a primitive whose origin is its geometric centre."""
    r = rotation_matrix(rotation)
    half = [d / 2 for d in dimensions]
    ext = [sum(abs(r[i][j]) * half[j] for j in range(3)) for i in range(3)]
    return Box(
        (location[0] - ext[0], location[1] - ext[1], location[2] - ext[2]),
        (location[0] + ext[0], location[1] + ext[1], location[2] + ext[2]),
    )


def object_box(o: ObjectSnapshot) -> Box:
    return world_box(tuple(o.location), tuple(o.dimensions), tuple(o.rotation))  # type: ignore[arg-type]


def _r(v: Any, nd: int = 4) -> Any:
    if isinstance(v, float):
        return round(v, nd) + 0.0
    if isinstance(v, (list, tuple)):
        return [_r(x, nd) for x in v]
    return v


def scene_report(inv: SceneInventory) -> dict[str, Any]:
    """Token-lean description used by every LLM agent: ids, hierarchy, sizes, positions, world
    bounds (so the model can place parts flush against each other) and materials with use counts."""
    users: dict[str, list[str]] = {}
    for o in inv.objects:
        for m in o.material_ids:
            users.setdefault(m, []).append(o.object_id)
    objects = []
    for o in inv.objects:
        box = object_box(o)
        objects.append(
            {
                "id": o.object_id,
                "name": o.name,
                **({"component": o.component_type} if o.component_type else {}),
                **({"parent": o.parent_id} if o.parent_id else {}),
                "size": _r(list(o.dimensions)),
                "position": _r(list(o.location)),
                **({"rotation": _r(list(o.rotation), 2)} if any(abs(a) > 1e-6 for a in o.rotation) else {}),
                "bounds": {"min": _r(list(box.min)), "max": _r(list(box.max))},
                "material": o.material_ids[0] if o.material_ids else None,
                **({"hidden": True} if not o.visible else {}),
                **({"modifiers": [m.type.lower() for m in o.modifiers]} if o.modifiers else {}),
            }
        )
    materials = [
        {
            "id": m.material_id,
            "name": m.name,
            "color": m.base_color,
            "metallic": _r(m.metallic, 2),
            "roughness": _r(m.roughness, 2),
            **({"alpha": _r(m.alpha, 2)} if m.alpha < 1 else {}),
            "used_by": users.get(m.material_id, []),
        }
        for m in inv.materials
    ]
    report: dict[str, Any] = {"objects": objects, "materials": materials}
    if inv.objects:
        boxes = [object_box(o) for o in inv.objects]
        report["scene_bounds"] = {
            "min": _r([min(b.min[i] for b in boxes) for i in range(3)]),
            "max": _r([max(b.max[i] for b in boxes) for i in range(3)]),
        }
    return report


def summarize(inv: SceneInventory) -> str:
    """One line for progress events / logs."""
    if not inv.objects:
        return "empty scene"
    comps = sorted({o.component_type for o in inv.objects if o.component_type})
    return f"{len(inv.objects)} objects, {len(inv.materials)} materials" + (f" ({', '.join(comps)})" if comps else "")
