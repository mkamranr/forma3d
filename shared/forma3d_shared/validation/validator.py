"""DSL batch validator (spec §8.5).

1. Structural: strict Pydantic parsing (types, ranges, allowlisted operation types, unknown fields).
2. Semantic: replays every operation against a symbolic copy of the current scene inventory to
   check references, id collisions, hierarchy cycles, dependency ordering and complexity limits.

All issues are collected (not fail-fast) so callers — including LLM repair loops — get a complete
picture. On success an immutable `ValidatedBatch` with a content hash is returned.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Any, Union

from pydantic import ValidationError

from forma3d_shared.constants.limits import ModelingLimits
from forma3d_shared.schemas.modeling_operation import (
    BOOLEAN_TYPES,
    CREATE_PRIMITIVE_TYPES,
    MESH_ONLY_TYPES,
    Operation,
    OperationBatch,
)
from forma3d_shared.schemas.scene_object import SceneInventory


@dataclass(frozen=True)
class ValidationIssue:
    code: str
    message: str
    operation_id: str | None = None
    location: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {k: v for k, v in self.__dict__.items() if v is not None}


class BatchValidationError(Exception):
    def __init__(self, issues: list[ValidationIssue]) -> None:
        self.issues = issues
        super().__init__("; ".join(i.message for i in issues[:5]))

    def as_dicts(self) -> list[dict[str, Any]]:
        return [i.as_dict() for i in self.issues]


@dataclass(frozen=True)
class ValidatedBatch:
    batch: OperationBatch
    canonical_operations: tuple[dict[str, Any], ...]
    sha256: str
    stats: dict[str, int]

    @property
    def operations(self) -> list[Operation]:
        return list(self.batch.operations)


# --------------------------------------------------------------------------- symbolic scene


@dataclass
class _Obj:
    parent: str | None
    kind: str
    vertices: int
    children: set[str] = field(default_factory=set)


class _Scene:
    def __init__(self, inventory: SceneInventory) -> None:
        self.objects: dict[str, _Obj] = {}
        for o in inventory.objects:
            self.objects[o.object_id] = _Obj(parent=o.parent_id, kind=o.type, vertices=o.mesh.vertices)
        for oid, o in self.objects.items():
            if o.parent and o.parent in self.objects:
                self.objects[o.parent].children.add(oid)
        self.materials: set[str] = {m.material_id for m in inventory.materials}
        self.collections: set[str] = {c.collection_id for c in inventory.collections}

    def ancestors(self, oid: str) -> set[str]:
        out: set[str] = set()
        cur = self.objects[oid].parent
        while cur and cur in self.objects and cur not in out:
            out.add(cur)
            cur = self.objects[cur].parent
        return out

    def descendants(self, oid: str) -> set[str]:
        out: set[str] = set()
        stack = list(self.objects[oid].children)
        while stack:
            cur = stack.pop()
            if cur in out or cur not in self.objects:
                continue
            out.add(cur)
            stack.extend(self.objects[cur].children)
        return out

    def add(self, oid: str, parent: str | None, vertices: int, kind: str = "MESH") -> None:
        self.objects[oid] = _Obj(parent=parent, kind=kind, vertices=vertices)
        if parent:
            self.objects[parent].children.add(oid)

    def set_parent(self, oid: str, parent: str | None) -> None:
        old = self.objects[oid].parent
        if old and old in self.objects:
            self.objects[old].children.discard(oid)
        self.objects[oid].parent = parent
        if parent:
            self.objects[parent].children.add(oid)

    def remove(self, oid: str) -> None:
        self.set_parent(oid, None)
        del self.objects[oid]

    @property
    def total_vertices(self) -> int:
        return sum(o.vertices for o in self.objects.values())


def _primitive_vertices(op: Any) -> int:
    p = op.parameters
    t = op.operation_type
    if t == "create_cube":
        return 8
    if t == "create_plane":
        return 4
    if t == "create_sphere":
        return p.segments * (p.rings - 1) + 2
    if t in ("create_cylinder", "create_cone"):
        return p.vertices * 2
    if t == "create_torus":
        return p.major_segments * p.minor_segments
    if t == "create_circle":
        return p.vertices + (1 if p.fill == "triangle_fan" else 0)
    if t == "create_mesh":
        return len(p.vertices)
    if t == "create_curve":
        segments = len(p.points) * (p.resolution if p.curve_type == "smooth" else 1)
        ring = 4 + 2 * p.profile_resolution
        return (segments + 1) * ring * 2 + 2
    return 0


def referenced_image_ids(operations: list[Any]) -> list[str]:
    """Uploaded texture images a batch uses (the backend resolves them to stored files)."""
    out: list[str] = []
    for op in operations:
        t = op.get("operation_type") if isinstance(op, dict) else op.operation_type
        if t != "assign_texture":
            continue
        p = op.get("parameters", {}) if isinstance(op, dict) else op.parameters.model_dump()
        if p.get("image_id") and p["image_id"] not in out:
            out.append(p["image_id"])
    return out


# --------------------------------------------------------------------------- entry point


def _loc(parts: tuple[Union[int, str], ...]) -> str:
    return ".".join(str(p) for p in parts)


def validate_batch(
    payload: Union[OperationBatch, dict[str, Any]],
    inventory: SceneInventory | None = None,
    limits: ModelingLimits | None = None,
) -> ValidatedBatch:
    """Validate a batch against the current scene. Raises `BatchValidationError`."""
    limits = limits or ModelingLimits()
    inventory = inventory or SceneInventory()

    # 1. Structural
    if isinstance(payload, OperationBatch):
        batch = payload
    else:
        try:
            batch = OperationBatch.model_validate(payload)
        except ValidationError as exc:
            issues = []
            for err in exc.errors()[:50]:
                loc = err.get("loc", ())
                op_id = None
                if len(loc) >= 2 and loc[0] == "operations" and isinstance(loc[1], int):
                    raw_ops = payload.get("operations") if isinstance(payload, dict) else None
                    if isinstance(raw_ops, list) and loc[1] < len(raw_ops) and isinstance(raw_ops[loc[1]], dict):
                        op_id = (
                            str(raw_ops[loc[1]].get("operation_id")) if raw_ops[loc[1]].get("operation_id") else None
                        )
                code = "unknown_operation" if err.get("type") == "union_tag_invalid" else "schema"
                issues.append(
                    ValidationIssue(
                        code=code, message=err.get("msg", "invalid"), operation_id=op_id, location=_loc(loc)
                    )
                )
            raise BatchValidationError(issues) from exc

    issues: list[ValidationIssue] = []

    def fail(code: str, message: str, op_id: str | None = None) -> None:
        issues.append(ValidationIssue(code=code, message=message, operation_id=op_id))

    if len(batch.operations) > limits.max_operations_per_batch:
        fail(
            "limit_operations",
            f"batch has {len(batch.operations)} operations (limit {limits.max_operations_per_batch})",
        )

    scene = _Scene(inventory)
    seen_ops: set[str] = set()
    new_objects = 0
    touched: set[str] = set()

    def need_object(oid: str, op_id: str, what: str = "target") -> bool:
        if oid not in scene.objects:
            fail("unknown_object", f"{what} object '{oid}' does not exist", op_id)
            return False
        return True

    def need_material(mid: str, op_id: str) -> bool:
        if mid not in scene.materials:
            fail("unknown_material", f"material '{mid}' does not exist", op_id)
            return False
        return True

    def need_collection(cid: str, op_id: str) -> bool:
        if cid not in scene.collections:
            fail("unknown_collection", f"collection '{cid}' does not exist", op_id)
            return False
        return True

    def need_kind(oid: str, kind: str, op_id: str, t: str, what: str = "target") -> bool:
        if not need_object(oid, op_id, what):
            return False
        actual = scene.objects[oid].kind
        if actual != kind:
            fail(f"not_a_{kind.lower()}", f"{t} requires a {kind.lower()} {what}; '{oid}' is {actual}", op_id)
            return False
        return True

    def grow(oid: str, factor: float) -> None:
        scene.objects[oid].vertices = int(scene.objects[oid].vertices * factor)

    for op in batch.operations:
        oid_ = op.operation_id
        t = op.operation_type
        p: Any = op.parameters

        if oid_ in seen_ops:
            fail("duplicate_operation_id", f"operation_id '{oid_}' is used more than once", oid_)
        for dep in op.dependencies:
            if dep not in seen_ops:
                fail("bad_dependency", f"dependency '{dep}' must reference an earlier operation in this batch", oid_)
        seen_ops.add(oid_)
        target = op.target_object_id

        if t in MESH_ONLY_TYPES:
            assert target is not None
            if not need_kind(target, "MESH", oid_, t):
                continue

        if t in CREATE_PRIMITIVE_TYPES or t in ("add_camera", "add_light"):
            assert target is not None
            if target in scene.objects:
                fail("object_exists", f"object '{target}' already exists", oid_)
                continue
            ok = True
            if p.parent_id is not None:
                ok &= need_object(p.parent_id, oid_, "parent")
            if getattr(p, "collection_id", None) is not None:
                ok &= need_collection(p.collection_id, oid_)
            if getattr(p, "material_id", None) is not None:
                ok &= need_material(p.material_id, oid_)
            verts = _primitive_vertices(op)
            kind = {"add_camera": "CAMERA", "add_light": "LIGHT"}.get(t, "MESH")
            scene.add(target, p.parent_id if ok else None, verts, kind)
            new_objects += 1
            touched.add(target)

        elif t == "duplicate_object":
            assert target is not None
            if not need_object(target, oid_):
                continue
            if p.new_object_id in scene.objects:
                fail("object_exists", f"object '{p.new_object_id}' already exists", oid_)
                continue
            src = scene.objects[target]
            scene.add(p.new_object_id, src.parent, src.vertices, src.kind)
            new_objects += 1
            touched.add(p.new_object_id)

        elif t == "delete_object":
            assert target is not None
            if not need_object(target, oid_):
                continue
            descendants = scene.descendants(target)
            if descendants and not p.recursive:
                fail(
                    "has_children",
                    f"object '{target}' has children ({', '.join(sorted(descendants)[:5])}); "
                    "unparent them first or set recursive=true",
                    oid_,
                )
                continue
            for d in descendants:
                scene.remove(d)
            scene.remove(target)

        elif t == "parent_object":
            assert target is not None
            if not (need_object(target, oid_) & need_object(p.parent_id, oid_, "parent")):
                continue
            if p.parent_id == target or p.parent_id in scene.descendants(target):
                fail("hierarchy_cycle", f"parenting '{target}' to '{p.parent_id}' would create a cycle", oid_)
                continue
            scene.set_parent(target, p.parent_id)
            touched.add(target)

        elif t == "create_collection":
            if p.collection_id in scene.collections:
                fail("collection_exists", f"collection '{p.collection_id}' already exists", oid_)
                continue
            if p.parent_collection_id is not None and not need_collection(p.parent_collection_id, oid_):
                continue
            scene.collections.add(p.collection_id)

        elif t == "move_to_collection":
            assert target is not None
            need_object(target, oid_)
            need_collection(p.collection_id, oid_)
            touched.add(target)

        elif t == "create_material":
            if p.material_id in scene.materials:
                fail("material_exists", f"material '{p.material_id}' already exists", oid_)
                continue
            scene.materials.add(p.material_id)

        elif t in ("update_material", "set_base_color", "set_metallic", "set_roughness", "set_transparency"):
            need_material(p.material_id, oid_)

        elif t == "assign_material":
            assert target is not None
            need_object(target, oid_)
            need_material(p.material_id, oid_)
            touched.add(target)

        elif t in ("update_camera", "update_light"):
            assert target is not None
            if need_kind(target, "CAMERA" if t == "update_camera" else "LIGHT", oid_, t):
                touched.add(target)

        elif t in ("assign_texture", "remove_texture"):
            need_material(p.material_id, oid_)

        elif t == "set_world_background":
            pass

        elif t in BOOLEAN_TYPES:
            assert target is not None
            operand = p.operand_id
            if operand == target:
                fail("invalid_operand", f"{t}: an object cannot be combined with itself", oid_)
                continue
            if not need_kind(operand, "MESH", oid_, t, "operand"):
                continue
            if not p.keep_operand:
                if operand in scene.ancestors(target):
                    fail("invalid_operand", f"{t}: operand '{operand}' is a parent of '{target}'; keep it", oid_)
                    continue
                kids = scene.descendants(operand)
                if kids:
                    fail(
                        "has_children",
                        f"operand '{operand}' has children ({', '.join(sorted(kids)[:5])}); "
                        "unparent them first or set keep_operand=true",
                        oid_,
                    )
                    continue
            scene.objects[target].vertices += scene.objects[operand].vertices
            if not p.keep_operand:
                scene.remove(operand)
                touched.discard(operand)
            touched.add(target)

        elif t in ("extrude", "inset"):
            assert target is not None
            grow(target, 2)
            touched.add(target)

        elif t in ("subdivide", "mirror", "array", "remesh", "decimate"):
            assert target is not None
            if t == "subdivide":
                if p.levels > limits.max_subdivision_levels:
                    fail(
                        "limit_subdivision",
                        f"subdivision levels {p.levels} exceeds limit {limits.max_subdivision_levels}",
                        oid_,
                    )
                grow(target, 4**p.levels)
            elif t == "mirror":
                if p.mirror_object_id is not None and not need_object(p.mirror_object_id, oid_, "mirror"):
                    continue
                grow(target, 2 ** len(p.axes))
            elif t == "array":
                if p.count > limits.max_array_count:
                    fail("limit_array", f"array count {p.count} exceeds limit {limits.max_array_count}", oid_)
                grow(target, p.count)
            elif t == "decimate":
                grow(target, p.ratio)
            touched.add(target)

        elif t == "join_objects":
            assert target is not None
            ok = True
            for other in p.object_ids:
                if other == target:
                    fail("invalid_operand", "join_objects: object_ids must not include the target", oid_)
                    ok = False
                elif not need_kind(other, "MESH", oid_, t, "joined"):
                    ok = False
                elif other in scene.ancestors(target):
                    fail("invalid_operand", f"join_objects: '{other}' is a parent of '{target}'", oid_)
                    ok = False
            if not ok:
                continue
            for other in p.object_ids:
                for child in list(scene.objects[other].children):
                    scene.set_parent(child, target)
                scene.objects[target].vertices += scene.objects[other].vertices
                scene.remove(other)
                touched.discard(other)
            touched.add(target)

        elif t == "separate_objects":
            assert target is not None
            clash = [n for n in p.new_object_ids if n in scene.objects]
            if clash:
                fail("object_exists", f"object '{clash[0]}' already exists", oid_)
                continue
            src = scene.objects[target]
            share = max(src.vertices // (len(p.new_object_ids) + 1), 1)
            for new_id in p.new_object_ids:
                scene.add(new_id, src.parent, share)
                new_objects += 1
                touched.add(new_id)
            touched.add(target)

        elif t in ("bevel", "solidify"):
            assert target is not None
            if t == "bevel":
                if p.segments > limits.max_bevel_segments:
                    fail(
                        "limit_bevel_segments",
                        f"bevel segments {p.segments} exceeds limit {limits.max_bevel_segments}",
                        oid_,
                    )
                # Rough upper bound on the evaluated mesh growth.
                scene.objects[target].vertices *= 1 + p.segments
            touched.add(target)

        else:  # target-only transforms / rename / unparent / visibility
            assert target is not None
            if need_object(target, oid_):
                if t == "unparent_object":
                    scene.set_parent(target, None)
                touched.add(target)

    # Complexity limits
    if new_objects > limits.max_new_objects_per_batch:
        fail("limit_new_objects", f"batch creates {new_objects} objects (limit {limits.max_new_objects_per_batch})")
    if len(scene.objects) > limits.max_scene_objects:
        fail(
            "limit_scene_objects",
            f"scene would contain {len(scene.objects)} objects (limit {limits.max_scene_objects})",
        )
    if len(scene.materials) > limits.max_scene_materials:
        fail(
            "limit_materials",
            f"scene would contain {len(scene.materials)} materials (limit {limits.max_scene_materials})",
        )
    for oid, obj in scene.objects.items():
        if oid in touched and obj.vertices > limits.max_vertices_per_object:
            fail(
                "limit_vertices",
                f"object '{oid}' would have ~{obj.vertices} vertices (limit {limits.max_vertices_per_object})",
            )
    if scene.total_vertices > limits.max_scene_vertices:
        fail(
            "limit_scene_vertices",
            f"scene would have ~{scene.total_vertices} vertices (limit {limits.max_scene_vertices})",
        )

    if issues:
        raise BatchValidationError(issues)

    canonical = tuple(op.model_dump(mode="json") for op in batch.operations)
    digest = hashlib.sha256(
        json.dumps(
            {"schema_version": batch.schema_version, "operations": canonical}, sort_keys=True, separators=(",", ":")
        ).encode()
    ).hexdigest()
    return ValidatedBatch(
        batch=batch,
        canonical_operations=canonical,
        sha256=digest,
        stats={
            "operations": len(batch.operations),
            "new_objects": new_objects,
            "scene_objects": len(scene.objects),
            "estimated_vertices": scene.total_vertices,
        },
    )
