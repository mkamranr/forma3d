"""Transforms. Locations are world-space; rotations are local XYZ Euler degrees."""

from __future__ import annotations

import math

from mathutils import Euler, Matrix, Vector

from .context import OpError, RuntimeContext


def move_object(ctx: RuntimeContext, op: dict) -> None:
    obj = ctx.obj(op["target_object_id"])
    p = op["parameters"]
    ctx.update()
    if p.get("location") is not None:
        obj.matrix_world.translation = Vector(p["location"])
    else:
        obj.matrix_world.translation = obj.matrix_world.translation + Vector(p["offset"])
    ctx.update()
    ctx.mark_modified(obj)


def rotate_object(ctx: RuntimeContext, op: dict) -> None:
    obj = ctx.obj(op["target_object_id"])
    p = op["parameters"]
    obj.rotation_mode = "XYZ"
    if p.get("rotation") is not None:
        obj.rotation_euler = Euler([math.radians(a) for a in p["rotation"]], "XYZ")
    else:
        cur = obj.rotation_euler
        obj.rotation_euler = Euler([cur[i] + math.radians(p["delta"][i]) for i in range(3)], "XYZ")
    ctx.update()
    ctx.mark_modified(obj)


def scale_object(ctx: RuntimeContext, op: dict) -> None:
    obj = ctx.obj(op["target_object_id"])
    p = op["parameters"]
    if p.get("scale") is not None:
        obj.scale = Vector(p["scale"])
    else:
        obj.scale = obj.scale * float(p["factor"])
    ctx.update()
    ctx.mark_modified(obj)


def set_dimensions(ctx: RuntimeContext, op: dict) -> None:
    """Resize the mesh data (not object scale) so the base mesh's bounding box times the object
    scale equals the requested dimensions. Origin and modifiers are preserved."""
    obj = ctx.obj(op["target_object_id"])
    if obj.type != "MESH":
        raise OpError("not_a_mesh", f"set_dimensions requires a mesh; '{obj.name}' is {obj.type}")
    mesh = obj.data
    if len(mesh.vertices) == 0:
        raise OpError("empty_mesh", f"object '{obj.name}' has no geometry")
    target = op["parameters"]["dimensions"]
    factors = []
    for axis in range(3):
        coords = [v.co[axis] for v in mesh.vertices]
        current = (max(coords) - min(coords)) * abs(obj.scale[axis])
        if current < 1e-9:
            raise OpError(
                "flat_axis",
                f"object '{obj.name}' is flat along {'XYZ'[axis]}; use solidify to give it thickness",
            )
        factors.append(target[axis] / current)
    if mesh.users > 1:
        obj.data = mesh = mesh.copy()  # never resize geometry shared with another object
    mesh.transform(Matrix.Diagonal((*factors, 1.0)))
    mesh.update()
    ctx.update()
    ctx.mark_modified(obj)
