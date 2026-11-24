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
