"""Modeling Agent (deterministic): component plan -> ordered DSL operations + expected effects.

Converting design decisions into operations is mechanical, so it is done in code rather than by
the LLM: stable ids are resolved against the scene, primitives are chosen and parameterised,
dependencies are generated, and the expected result is recorded for the QA agent. No Python or
shell is ever produced; the output is plain DSL that still goes through the shared validator.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from forma3d_shared import DSL_SCHEMA_VERSION
from forma3d_shared.schemas.scene_object import SceneInventory

from .inspection import local_rotation, object_box, world_rotations, world_space
from .plan import PartSpec, Plan

EPS = 1e-6
#: pattern repeats per meter (wood grain boards ~0.5 m, bricks ~0.25 m, ...)
TEXTURE_SCALE = {
    "wood": 1.5,
    "marble": 1.0,
    "noise": 2.0,
    "checker": 2.0,
    "brick": 1.0,
    "fabric": 3.0,
    "concrete": 1.0,
    "brushed_metal": 2.0,
}
LIGHT_ENERGY = {"point": 300.0, "spot": 600.0, "area": 300.0, "sun": 3.0}


def _title(identifier: str) -> str:
    return identifier.replace("_", " ").strip().title()[:64] or "Part"


@dataclass
class Expectations:
    created: dict[str, dict[str, Any]] = field(default_factory=dict)
    modified: dict[str, dict[str, Any]] = field(default_factory=dict)
    deleted: set[str] = field(default_factory=set)
    #: objects allowed to differ from the previous version (targets and their moved descendants)
    affected: set[str] = field(default_factory=set)
    materials_created: dict[str, dict[str, Any]] = field(default_factory=dict)
    materials_updated: dict[str, dict[str, Any]] = field(default_factory=dict)
    relations: list[dict[str, str]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "created": self.created,
            "modified": self.modified,
            "deleted": sorted(self.deleted),
            "affected": sorted(self.affected),
            "materials_created": self.materials_created,
            "materials_updated": self.materials_updated,
            "relations": self.relations,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> Expectations:
        return cls(
            created=d.get("created", {}),
            modified=d.get("modified", {}),
            deleted=set(d.get("deleted", [])),
            affected=set(d.get("affected", [])),
            materials_created=d.get("materials_created", {}),
            materials_updated=d.get("materials_updated", {}),
            relations=d.get("relations", []),
        )


@dataclass
class Compiled:
    operations: list[dict[str, Any]]
    expectations: Expectations
    summary: str

    def batch(self) -> dict[str, Any]:
        return {"schema_version": DSL_SCHEMA_VERSION, "summary": self.summary, "operations": self.operations}


class PlanError(Exception):
    def __init__(self, issues: list[str]) -> None:
        super().__init__("; ".join(issues))
        self.issues = issues


# --------------------------------------------------------------------------- plan validation
def check_plan(plan: Plan, inv: SceneInventory) -> list[str]:
    """Plan-level problems the Planning Agent can fix (reported back to it verbatim)."""
    issues: list[str] = []
    existing = {o.object_id for o in inv.objects}
    mats = {m.material_id for m in inv.materials}
    created = {p.id for p in plan.parts if p.action == "create"}
    seen: set[str] = set()
    cutters = {p.id for p in plan.parts if p.action == "create" and p.cut_into}
    for p in plan.parts:
        if p.cut_into is not None:
            if p.action != "create":
                issues.append(f"'{p.id}': cut_into only applies to new parts (the cutting tool)")
            elif p.cut_into not in (existing | created) - cutters:
                issues.append(f"'{p.id}' cut_into '{p.cut_into}' is not an existing or new solid part")
            elif p.shape == "light":
                issues.append(f"'{p.id}': a light cannot cut a part")
        # a new light without a position is placed above the model by compile_plan
        if p.id in seen:
            issues.append(f"part '{p.id}' appears more than once; merge its changes into one entry")
        seen.add(p.id)
        if p.action == "create":
            if p.id in existing:
                issues.append(f"'{p.id}' already exists; use action 'modify' or choose a new id")
            if p.shape is None:
                issues.append(f"create '{p.id}': 'shape' is required")
            if p.shape != "light" and (p.size is None or any(v <= 0 for v in p.size[: 2 if p.shape == "plane" else 3])):
                issues.append(f"create '{p.id}': 'size' needs 3 positive numbers in meters")
            if p.position is None and p.shape != "light":
                issues.append(f"create '{p.id}': 'position' (world centre, meters) is required")
            if p.shape == "torus" and p.size and p.size[2] * 2 >= min(p.size[0], p.size[1]):
                issues.append(f"torus '{p.id}': size[2] is the tube thickness and must be < half the diameter")
        elif p.id not in existing:
            issues.append(f"'{p.id}' does not exist in the scene; use an existing id (or action 'create')")
        if p.action == "modify" and not any(
            v is not None
            for v in (
                p.name,
                p.size,
                p.position,
                p.rotation,
                p.material,
                p.parent,
                p.visible,
                p.bevel,
                p.energy,
                p.color,
            )
        ):
            issues.append(f"modify '{p.id}' changes nothing; set at least one field")
        if p.material and p.material not in mats and p.material not in {m.id for m in plan.materials}:
            issues.append(f"'{p.id}' uses material '{p.material}' that is neither in the scene nor in 'materials'")
        if p.parent and p.parent not in existing | created:
            issues.append(f"'{p.id}' parent '{p.parent}' does not exist")
        if p.parent and p.parent == p.id:
            issues.append(f"'{p.id}' cannot be its own parent")
    for m in plan.materials:
        if m.id not in mats and m.color is None:
            issues.append(f"new material '{m.id}' needs a 'color' (#RRGGBB)")
    alive = (existing | created) - {p.id for p in plan.parts if p.action == "delete"} - cutters
    for r in plan.relations:
        for ref in (r.subject, r.object):
            if ref not in alive:
                issues.append(f"relation {r.subject} {r.relation} {r.object}: '{ref}' is not in the result")
    if not plan.parts and not plan.materials:
        issues.append("the plan contains no changes")
    return issues


# --------------------------------------------------------------------------- compiler
class _Builder:
    def __init__(self) -> None:
        self.ops: list[dict[str, Any]] = []
        self.made_by: dict[str, str] = {}  # object/material id -> operation id that created it

    def add(
        self,
        op_type: str,
        target: str | None,
        params: dict[str, Any],
        deps: list[str | None] = (),
        meta: dict[str, Any] | None = None,
    ) -> str:
        op_id = f"op_{len(self.ops) + 1:03d}"
        op: dict[str, Any] = {
            "operation_id": op_id,
            "operation_type": op_type,
            "parameters": params,
            "dependencies": sorted({d for d in deps if d}),
        }
        if target is not None:
            op["target_object_id"] = target
        if meta:
            op["metadata"] = meta
        self.ops.append(op)
        return op_id


def _create_params(p: PartSpec) -> tuple[str, dict[str, Any], bool]:
    """(operation type, parameters, needs explicit set_dimensions)."""
    size = list(p.size or [1, 1, 1])
    uneven = abs(size[0] - size[1]) > EPS
    if p.shape == "box":
        return "create_cube", {"dimensions": size}, False
    if p.shape == "cylinder":
        return "create_cylinder", {"radius": size[0] / 2, "depth": size[2]}, uneven
    if p.shape == "cone":
        return "create_cone", {"radius_bottom": size[0] / 2, "depth": size[2]}, uneven
    if p.shape == "sphere":
        return "create_sphere", {"radius": size[0] / 2}, uneven or abs(size[2] - size[0]) > EPS
    if p.shape == "torus":
        minor = size[2] / 2
        return "create_torus", {"major_radius": size[0] / 2 - minor, "minor_radius": minor}, uneven
    if p.shape == "plane":
        return "create_plane", {"size": [size[0], size[1]]}, False
    raise PlanError([f"create '{p.id}': unknown shape {p.shape!r}"])


def _descendants(inv: SceneInventory, roots: set[str]) -> set[str]:
    children: dict[str, list[str]] = {}
    for o in inv.objects:
        if o.parent_id:
            children.setdefault(o.parent_id, []).append(o.object_id)
    out: set[str] = set()
    stack = [c for r in roots for c in children.get(r, [])]
    while stack:
        cur = stack.pop()
        if cur not in out:
            out.add(cur)
            stack.extend(children.get(cur, []))
    return out


def default_light_position(inv: SceneInventory) -> list[float]:
    """Above the model's centre, 1.2 m higher than its top (models often omit a light's position)."""
    boxes = [object_box(o) for o in world_space(inv).objects if o.type == "MESH"]
    if not boxes:
        return [0.0, 0.0, 2.5]
    lo = [min(b.min[i] for b in boxes) for i in range(3)]
    hi = [max(b.max[i] for b in boxes) for i in range(3)]
    return [round((lo[0] + hi[0]) / 2, 3), round((lo[1] + hi[1]) / 2, 3), round(hi[2] + 1.2, 3)]


def compile_plan(plan: Plan, inv: SceneInventory) -> Compiled:
    if any(p.shape == "light" and p.action == "create" and p.position is None for p in plan.parts):
        where = default_light_position(inv)
        parts = [
            p.model_copy(update={"position": where})
            if p.shape == "light" and p.action == "create" and p.position is None
            else p
            for p in plan.parts
        ]
        plan = plan.model_copy(update={"parts": parts})
    issues = check_plan(plan, inv)
    if issues:
        raise PlanError(issues)
    existing_mats = {m.material_id for m in inv.materials}
    b = _Builder()
    exp = Expectations(relations=[r.model_dump() for r in plan.relations])

    textured = {m.material_id for m in inv.materials if m.texture}
    for m in plan.materials:
        extras = {"emission_strength": m.emission, "transmission": m.transmission}
        if m.id in existing_mats:
            fields = {
                k: v
                for k, v in {
                    "name": m.name,
                    "base_color": m.color,
                    "metallic": m.metallic,
                    "roughness": m.roughness,
                    "alpha": m.alpha,
                    **extras,
                }.items()
                if v is not None
            }
            if fields:
                b.made_by[m.id] = b.add("update_material", None, {"material_id": m.id, **fields})
                exp.materials_updated[m.id] = fields
        else:
            params = {"material_id": m.id, "name": m.name or _title(m.id), "base_color": m.color.upper()}  # type: ignore[union-attr]
            for k, v in (("metallic", m.metallic), ("roughness", m.roughness), ("alpha", m.alpha), *extras.items()):
                if v is not None:
                    params[k] = v
            b.made_by[m.id] = b.add("create_material", None, params)
            exp.materials_created[m.id] = dict(params)
        if m.texture:
            b.made_by[m.id] = b.add(
                "assign_texture",
                None,
                {"material_id": m.id, "pattern": m.texture, "scale": TEXTURE_SCALE.get(m.texture, 2.0)},
                [b.made_by.get(m.id)],
            )
            (exp.materials_updated if m.id in existing_mats else exp.materials_created).setdefault(m.id, {})[
                "texture"
            ] = m.texture
        elif m.texture == "" and m.id in textured:
            b.made_by[m.id] = b.add("remove_texture", None, {"material_id": m.id}, [b.made_by.get(m.id)])

    # Creates, parents before children.
    creates = [p for p in plan.parts if p.action == "create"]
    pending = {p.id: p for p in creates}
    ordered: list[PartSpec] = []
    while pending:
        ready = [p for p in pending.values() if not p.parent or p.parent not in pending]
        if not ready:
            raise PlanError(["parent relationships between new parts form a cycle"])
        for p in ready:
            ordered.append(p)
            pending.pop(p.id)
    for p in ordered:
        if p.shape == "light":
            params = {
                "name": p.name or _title(p.id),
                "light_type": p.light_type or "point",
                "location": p.position,
                "energy": p.energy if p.energy is not None else LIGHT_ENERGY[p.light_type or "point"],
                "color": (p.color or "#FFF4E5").upper(),
            }
            if p.rotation:
                params["rotation"] = p.rotation
            elif (p.light_type or "point") in ("spot", "area"):
                params["look_at"] = [p.position[0], p.position[1], 0.0]  # type: ignore[index]
            elif p.light_type == "sun":
                params["rotation"] = [40.0, 0.0, 30.0]
            if p.parent:
                params["parent_id"] = p.parent
            meta = {"component": p.component} if p.component else None
            b.made_by[p.id] = b.add("add_light", p.id, params, [b.made_by.get(p.parent or "")], meta)
            exp.created[p.id] = {"position": p.position, "parent": p.parent or None, "kind": "LIGHT"}
            continue
        op_type, params, fix_dims = _create_params(p)
        params.update(
            name=p.name or _title(p.id),
            location=p.position,
            rotation=p.rotation or [0.0, 0.0, 0.0],
        )
        if p.parent and not p.cut_into:  # cutting tools are consumed, never parented
            params["parent_id"] = p.parent
        if p.material and not p.cut_into:
            params["material_id"] = p.material
        deps = [b.made_by.get(p.parent or ""), b.made_by.get(p.material or "")]
        meta = {"component": p.component} if p.component else None
        op = b.add(op_type, p.id, params, deps, meta)
        if fix_dims:
            op = b.add("set_dimensions", p.id, {"dimensions": p.size}, [op])
        if p.bevel and not p.cut_into:
            op = b.add("bevel", p.id, {"width": p.bevel, "segments": 3}, [op])
        b.made_by[p.id] = op
        if p.cut_into:
            continue  # applied after every part exists (below)
        size = list(p.size or [])
        if p.shape == "plane":
            size = [size[0], size[1], 0.0]
        exp.created[p.id] = {
            "size": size,
            "position": p.position,
            "rotation": p.rotation or [0.0, 0.0, 0.0],
            "material": p.material,
            "parent": p.parent or None,
            "component": p.component,
        }

    moved_roots: set[str] = set()
    current = inv.object_map()
    world_rot = world_rotations(inv)
    planned = {q.id: q for q in plan.parts}

    def parent_world(oid: str, part: PartSpec) -> tuple[float, float, float]:
        """World rotation the part's parent will have once this plan has run."""
        parent = part.parent if part.parent is not None else (current[oid].parent_id if oid in current else None)
        if not parent:
            return (0.0, 0.0, 0.0)
        q = planned.get(parent)
        if q is not None and q.rotation is not None:
            return tuple(q.rotation)  # type: ignore[return-value]
        return world_rot.get(parent, (0.0, 0.0, 0.0))

    for p in plan.parts:
        if p.action != "modify":
            continue
        changes: dict[str, Any] = {}
        prev = b.made_by.get(p.id)
        if p.parent is not None:
            if p.parent == "":
                prev = b.add("unparent_object", p.id, {"keep_transform": True}, [prev])
            else:
                prev = b.add(
                    "parent_object",
                    p.id,
                    {"parent_id": p.parent, "keep_transform": True},
                    [prev, b.made_by.get(p.parent)],
                )
            changes["parent"] = p.parent or None
        if p.name is not None:
            prev = b.add("rename_object", p.id, {"name": p.name}, [prev])
            changes["name"] = p.name
        if p.energy is not None or p.color is not None:
            light = {
                k: v for k, v in (("energy", p.energy), ("color", (p.color or "").upper() or None)) if v is not None
            }
            prev = b.add("update_light", p.id, light, [prev])
            changes["light"] = light
        if p.size is not None:
            prev = b.add("set_dimensions", p.id, {"dimensions": p.size}, [prev])
            changes["size"] = p.size
        if p.rotation is not None:
            # plans give world rotations; rotate_object sets the rotation relative to the parent
            pw = parent_world(p.id, p)
            local = list(local_rotation(tuple(p.rotation), pw)) if any(abs(a) > 1e-9 for a in pw) else p.rotation  # type: ignore[arg-type]
            prev = b.add("rotate_object", p.id, {"rotation": local}, [prev])
            changes["rotation"] = p.rotation
            moved_roots.add(p.id)
        if p.position is not None:
            prev = b.add("move_object", p.id, {"location": p.position}, [prev])
            changes["position"] = p.position
            moved_roots.add(p.id)
        if p.material is not None:
            prev = b.add("assign_material", p.id, {"material_id": p.material}, [prev, b.made_by.get(p.material)])
            changes["material"] = p.material
        if p.visible is not None:
            prev = b.add("set_visibility", p.id, {"visible": p.visible}, [prev])
            changes["visible"] = p.visible
        if p.bevel:
            prev = b.add("bevel", p.id, {"width": p.bevel, "segments": 3}, [prev])
            changes["bevel"] = p.bevel
        exp.modified[p.id] = changes

    # Cutting tools: subtract each from its target (after the target's own changes), then it is gone.
    for p in ordered:
        if not p.cut_into:
            continue
        b.made_by[p.cut_into] = b.add(
            "boolean_difference",
            p.cut_into,
            {"operand_id": p.id},
            [b.made_by.get(p.cut_into), b.made_by.get(p.id)],
        )
        exp.deleted.add(p.id)
        if p.cut_into not in exp.created:
            exp.modified.setdefault(p.cut_into, {})["cut"] = p.id

    deleted_roots = {p.id for p in plan.parts if p.action == "delete"}
    for pid in deleted_roots:
        b.add("delete_object", pid, {"recursive": True})
    exp.deleted = exp.deleted | deleted_roots | _descendants(inv, deleted_roots)
    exp.affected = set(exp.modified) | _descendants(inv, moved_roots)
    summary = (plan.summary or "Update the model")[:200]
    return Compiled(b.ops, exp, summary)
