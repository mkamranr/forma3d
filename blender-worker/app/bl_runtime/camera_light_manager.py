"""Cameras, lights and the world background. Cameras and lights are tracked objects like meshes
(stable ids, hierarchy, transforms) and are exported to GLB as glTF cameras / punctual lights."""

from __future__ import annotations

import math

import bpy
from mathutils import Euler, Vector

from .context import PROP_LIGHT_COLOR, PROP_WORLD_COLOR, PROP_WORLD_STRENGTH, OpError, RuntimeContext
from .material_manager import hex_to_linear_rgba
from .object_factory import link_to_collection, set_parent, tag_new_object


def _aim(ctx: RuntimeContext, obj: bpy.types.Object, p: dict) -> None:
    obj.rotation_mode = "XYZ"
    if p.get("look_at") is not None:
        ctx.update()
        direction = Vector(p["look_at"]) - obj.matrix_world.translation
        if direction.length < 1e-6:
            raise OpError("invalid_aim", "look_at must differ from the location")
        world_rot = direction.to_track_quat("-Z", "Y").to_matrix().to_4x4()
        world_rot.translation = obj.matrix_world.translation
        obj.matrix_world = world_rot
    elif p.get("rotation") is not None:
        obj.rotation_euler = Euler([math.radians(a) for a in p["rotation"]], "XYZ")


def _place(ctx: RuntimeContext, obj: bpy.types.Object, p: dict) -> None:
    if p.get("location") is not None:
        ctx.update()
        obj.matrix_world.translation = Vector(p["location"])
    _aim(ctx, obj, p)
    ctx.update()


def _typed(ctx: RuntimeContext, op: dict, kind: str) -> bpy.types.Object:
    obj = ctx.obj(op["target_object_id"])
    if obj.type != kind:
        raise OpError(
            f"not_a_{kind.lower()}", f"{op['operation_type']} requires a {kind.lower()}; '{obj.name}' is {obj.type}"
        )
    return obj


def _new(ctx: RuntimeContext, op: dict, data) -> bpy.types.Object:
    object_id = op["target_object_id"]
    if object_id in ctx.objects:
        raise OpError("object_exists", f"object '{object_id}' already exists")
    p = op["parameters"]
    obj = bpy.data.objects.new(p["name"], data)
    link_to_collection(ctx, obj, None)
    component = (op.get("metadata") or {}).get("component")
    tag_new_object(ctx, obj, object_id, component if isinstance(component, str) else None)
    obj.location = Vector(p.get("location", (0.0, 0.0, 0.0)))
    ctx.update()
    _aim(ctx, obj, p)
    if p.get("parent_id"):
        set_parent(ctx, obj, ctx.obj(p["parent_id"]), keep_transform=True)
    ctx.update()
    return obj


# --------------------------------------------------------------------------- cameras
def add_camera(ctx: RuntimeContext, op: dict) -> None:
    p = op["parameters"]
    cam = bpy.data.cameras.new(p["name"])
    cam.lens = float(p.get("lens", 50.0))
    cam.clip_end = 1000.0
    obj = _new(ctx, op, cam)
    if p.get("set_active", True):
        ctx.scene.camera = obj


def update_camera(ctx: RuntimeContext, op: dict) -> None:
    obj = _typed(ctx, op, "CAMERA")
    p = op["parameters"]
    _place(ctx, obj, p)
    if p.get("lens") is not None:
        obj.data.lens = float(p["lens"])
    if p.get("set_active") is True:
        ctx.scene.camera = obj
    elif p.get("set_active") is False and ctx.scene.camera == obj:
        ctx.scene.camera = None
    ctx.mark_modified(obj)


# --------------------------------------------------------------------------- lights
def _light_props(light: bpy.types.Light, p: dict) -> None:
    if p.get("color") is not None:
        light[PROP_LIGHT_COLOR] = p["color"].upper()
        light.color = hex_to_linear_rgba(p["color"])[:3]
    if p.get("energy") is not None:
        light.energy = float(p["energy"])
    if p.get("radius") is not None:
        r = float(p["radius"])
        if light.type == "SUN":
            light.angle = min(math.radians(max(r, 0.0) * 10), math.pi)  # radius ~ angular softness
        elif light.type == "AREA":
            light.size = max(r, 0.01)
        else:
            light.shadow_soft_size = r
    if p.get("spot_angle") is not None and light.type == "SPOT":
        light.spot_size = math.radians(float(p["spot_angle"]))


def add_light(ctx: RuntimeContext, op: dict) -> None:
    p = op["parameters"]
    light = bpy.data.lights.new(p["name"], p.get("light_type", "point").upper())
    _light_props(
        light,
        {
            "color": p.get("color", "#FFFFFF"),
            "energy": p.get("energy", 500.0),
            "radius": p.get("radius", 0.1),
            "spot_angle": p.get("spot_angle", 45.0),
        },
    )
    _new(ctx, op, light)


def update_light(ctx: RuntimeContext, op: dict) -> None:
    obj = _typed(ctx, op, "LIGHT")
    p = op["parameters"]
    _place(ctx, obj, p)
    _light_props(obj.data, p)
    ctx.mark_modified(obj)


# --------------------------------------------------------------------------- world
def set_world_background(ctx: RuntimeContext, op: dict) -> None:
    p = op["parameters"]
    scene = ctx.scene
    if scene.world is None:
        scene.world = bpy.data.worlds.new("World")
    world = scene.world
    world.use_nodes = True
    bg = world.node_tree.nodes.get("Background") or world.node_tree.nodes.new("ShaderNodeBackground")
    color = p.get("color", "#202630").upper()
    strength = float(p.get("strength", 1.0))
    bg.inputs["Color"].default_value = (*hex_to_linear_rgba(color)[:3], 1.0)
    bg.inputs["Strength"].default_value = strength
    world.color = hex_to_linear_rgba(color)[:3]
    world[PROP_WORLD_COLOR] = color
    world[PROP_WORLD_STRENGTH] = strength
