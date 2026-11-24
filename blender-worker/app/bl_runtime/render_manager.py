"""CPU thumbnail rendering (Cycles). Runs *after* the .blend is saved, so the temporary camera and
lights never become part of the user's scene."""

from __future__ import annotations

import math

import bpy
from mathutils import Vector

from .context import PROP_OBJECT_ID


def _scene_bounds() -> tuple[Vector, float]:
    bpy.context.view_layer.update()
    pts = []
    for obj in bpy.data.objects:
        if PROP_OBJECT_ID in obj and obj.type == "MESH" and not obj.hide_render:
            pts.extend(obj.matrix_world @ Vector(c) for c in obj.bound_box)
    if not pts:
        return Vector((0, 0, 0.5)), 1.0
    lo = Vector((min(p.x for p in pts), min(p.y for p in pts), min(p.z for p in pts)))
    hi = Vector((max(p.x for p in pts), max(p.y for p in pts), max(p.z for p in pts)))
    return (lo + hi) / 2, max((hi - lo).length / 2, 0.05)


def render_thumbnail(path: str, size: int, samples: int = 24) -> None:
    scene = bpy.context.scene
    center, radius = _scene_bounds()

    cam_data = bpy.data.cameras.new("__thumb_cam")
    cam_data.lens = 50
    cam = bpy.data.objects.new("__thumb_cam", cam_data)
    scene.collection.objects.link(cam)
    fov = cam_data.angle
    distance = radius / math.sin(fov / 2) * 1.08
    direction = Vector((1.0, -1.35, 0.85)).normalized()  # three-quarter front view (front = -Y)
    cam.location = center + direction * distance
    cam.rotation_euler = (center - cam.location).to_track_quat("-Z", "Y").to_euler()
    cam_data.clip_end = distance * 10
    scene.camera = cam

    key = bpy.data.lights.new("__thumb_key", "SUN")
    key.energy = 3.0
    key_obj = bpy.data.objects.new("__thumb_key", key)
    key_obj.rotation_euler = (math.radians(50), 0, math.radians(35))
    scene.collection.objects.link(key_obj)

    if scene.world is None:
        scene.world = bpy.data.worlds.new("__thumb_world")
    scene.world.use_nodes = True
    bg = scene.world.node_tree.nodes.get("Background")
    if bg is not None:
        bg.inputs["Color"].default_value = (0.016, 0.019, 0.026, 1.0)  # ~#202630 (viewport bg)
        bg.inputs["Strength"].default_value = 1.0

    scene.render.engine = "CYCLES"
    scene.cycles.device = "CPU"
    scene.cycles.samples = samples
    scene.cycles.use_adaptive_sampling = True
    try:
        scene.cycles.use_denoising = True
        scene.cycles.denoiser = "OPENIMAGEDENOISE"
    except (AttributeError, TypeError):
        scene.cycles.use_denoising = False
    scene.render.resolution_x = size
    scene.render.resolution_y = size
    scene.render.resolution_percentage = 100
    scene.render.film_transparent = False
    scene.render.image_settings.file_format = "PNG"
    scene.render.image_settings.color_mode = "RGB"
    scene.render.filepath = path
    bpy.ops.render.render(write_still=True)
