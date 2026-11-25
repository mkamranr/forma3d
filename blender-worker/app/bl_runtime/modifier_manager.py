"""Non-destructive modifiers. Re-applying an operation updates the existing modifier (idempotent)."""

from __future__ import annotations

import math

import bpy

from .context import OpError, RuntimeContext


def _mesh_obj(ctx: RuntimeContext, op: dict) -> bpy.types.Object:
    obj = ctx.obj(op["target_object_id"])
    if obj.type != "MESH":
        raise OpError("not_a_mesh", f"{op['operation_type']} requires a mesh object")
    return obj


def bevel(ctx: RuntimeContext, op: dict) -> None:
    obj = _mesh_obj(ctx, op)
    p = op.get("parameters") or {}
    mod = obj.modifiers.get("Bevel") or obj.modifiers.new("Bevel", "BEVEL")
    mod.width = float(p.get("width", 0.01))
    mod.segments = int(p.get("segments", 3))
    mod.limit_method = "ANGLE" if p.get("limit_method", "angle") == "angle" else "NONE"
    mod.angle_limit = math.radians(float(p.get("angle", 30.0)))
    mod.harden_normals = False
    ctx.mark_modified(obj)


def solidify(ctx: RuntimeContext, op: dict) -> None:
    obj = _mesh_obj(ctx, op)
    p = op.get("parameters") or {}
    mod = obj.modifiers.get("Solidify") or obj.modifiers.new("Solidify", "SOLIDIFY")
    mod.thickness = float(p.get("thickness", 0.01))
    mod.offset = float(p.get("offset", -1.0))
    ctx.mark_modified(obj)


def _modifier(obj: bpy.types.Object, name: str, kind: str) -> bpy.types.Modifier:
    return obj.modifiers.get(name) or obj.modifiers.new(name, kind)


def subdivide(ctx: RuntimeContext, op: dict) -> None:
    obj = _mesh_obj(ctx, op)
    p = op.get("parameters") or {}
    mod = _modifier(obj, "Subdivision", "SUBSURF")
    mod.levels = mod.render_levels = int(p.get("levels", 2))
    mod.subdivision_type = "CATMULL_CLARK" if p.get("smooth", True) else "SIMPLE"
    ctx.mark_modified(obj)


def mirror(ctx: RuntimeContext, op: dict) -> None:
    obj = _mesh_obj(ctx, op)
    p = op.get("parameters") or {}
    axes = p.get("axes") or ["x"]
    mod = _modifier(obj, "Mirror", "MIRROR")
    for i, axis in enumerate("xyz"):
        mod.use_axis[i] = axis in axes
        mod.use_bisect_axis[i] = bool(p.get("bisect")) and axis in axes
    mod.mirror_object = ctx.obj(p["mirror_object_id"]) if p.get("mirror_object_id") else None
    mod.use_mirror_merge = bool(p.get("merge", True))
    ctx.mark_modified(obj)


def array(ctx: RuntimeContext, op: dict) -> None:
    obj = _mesh_obj(ctx, op)
    p = op.get("parameters") or {}
    mod = _modifier(obj, "Array", "ARRAY")
    mod.count = int(p.get("count", 2))
    mod.use_relative_offset = False
    mod.use_constant_offset = True
    mod.constant_offset_displace = tuple(float(v) for v in p.get("offset", (1.0, 0.0, 0.0)))
    mod.use_merge_vertices = bool(p.get("merge", False))
    ctx.mark_modified(obj)


def remesh(ctx: RuntimeContext, op: dict) -> None:
    obj = _mesh_obj(ctx, op)
    p = op.get("parameters") or {}
    voxel = float(p.get("voxel_size", 0.02))
    # Guard before Blender allocates the voxel grid: surface voxels ~ bounding-box area / voxel².
    x, y, z = (max(d, voxel) for d in obj.dimensions)
    estimate = int(2 * (x * y + y * z + x * z) / (voxel * voxel))
    limit = int(ctx.limits.get("max_vertices_per_object", 200_000))
    if estimate > limit:
        raise OpError(
            "remesh_too_dense",
            f"remeshing '{obj.name}' at {voxel} m would create ~{estimate} vertices (limit {limit}); "
            "use a larger voxel_size",
        )
    mod = _modifier(obj, "Remesh", "REMESH")
    mod.mode = "VOXEL"
    mod.voxel_size = voxel
    mod.use_smooth_shade = bool(p.get("smooth_shading", True))
    ctx.mark_modified(obj)


def decimate(ctx: RuntimeContext, op: dict) -> None:
    obj = _mesh_obj(ctx, op)
    p = op.get("parameters") or {}
    mod = _modifier(obj, "Decimate", "DECIMATE")
    mod.decimate_type = "COLLAPSE"
    mod.ratio = float(p.get("ratio", 0.5))
    ctx.mark_modified(obj)
