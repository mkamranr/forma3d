"""Rendering (spec §9.6). Everything here runs *after* the .blend is saved (thumbnails) or on a
read-only copy of a version (render jobs), so the temporary camera, studio lights and engine
settings never become part of the user's scene.

- Thumbnails: Cycles CPU, few samples, three-quarter framing.
- Render jobs: Cycles quality profiles (draft / standard / high) or Eevee; the scene's own camera
  (by id, or the active one) and lights when present, otherwise auto framing and a studio rig."""

from __future__ import annotations

import math
import os

import bpy
from mathutils import Vector

from .context import PROP_OBJECT_ID, PROP_WORLD_COLOR

VIEWPORT_BG = (0.016, 0.019, 0.026, 1.0)  # ~#202630


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


def _user_lights() -> list[bpy.types.Object]:
    return [o for o in bpy.data.objects if PROP_OBJECT_ID in o and o.type == "LIGHT" and not o.hide_render]


def _auto_camera(aspect: float) -> bpy.types.Object:
    scene = bpy.context.scene
    center, radius = _scene_bounds()
    cam_data = bpy.data.cameras.new("__f3d_cam")
    cam_data.lens = 50
    cam = bpy.data.objects.new("__f3d_cam", cam_data)
    scene.collection.objects.link(cam)
    fov = cam_data.angle if aspect >= 1 else 2 * math.atan(math.tan(cam_data.angle / 2) * aspect)
    distance = radius / math.sin(fov / 2) * 1.08
    direction = Vector((1.0, -1.35, 0.85)).normalized()  # three-quarter front view (front = -Y)
    cam.location = center + direction * distance
    cam.rotation_euler = (center - cam.location).to_track_quat("-Z", "Y").to_euler()
    cam_data.clip_end = distance * 10
    return cam


def _add_light(name: str, kind: str, energy: float, rotation=None, location=None, size: float = 1.0):
    data = bpy.data.lights.new(name, kind)
    data.energy = energy
    if kind == "AREA":
        data.size = size
    obj = bpy.data.objects.new(name, data)
    if rotation is not None:
        obj.rotation_euler = rotation
    if location is not None:
        obj.location = location
    bpy.context.scene.collection.objects.link(obj)
    return obj


def _studio_rig(center: Vector, radius: float) -> None:
    """Key / fill / rim lighting sized to the model, used only when the scene has no lights."""
    _add_light("__f3d_key", "SUN", 3.0, rotation=(math.radians(50), 0, math.radians(35)))
    d = radius * 3
    for name, offset, energy in (
        ("__f3d_fill", Vector((-1.2, -1.0, 0.6)), 60.0),
        ("__f3d_rim", Vector((0.3, 1.4, 1.2)), 90.0),
    ):
        loc = center + offset.normalized() * d
        light = _add_light(name, "AREA", energy * d * d, location=loc, size=radius * 2)
        light.rotation_euler = (center - loc).to_track_quat("-Z", "Y").to_euler()


def _ensure_world() -> None:
    scene = bpy.context.scene
    if scene.world is None:
        scene.world = bpy.data.worlds.new("__f3d_world")
    world = scene.world
    if PROP_WORLD_COLOR in world:
        return  # the user's background
    world.use_nodes = True
    bg = world.node_tree.nodes.get("Background")
    if bg is not None:
        bg.inputs["Color"].default_value = VIEWPORT_BG
        bg.inputs["Strength"].default_value = 1.0


GPU_BACKENDS = ("OPTIX", "CUDA", "HIP", "ONEAPI", "METAL")


def _gpu_device() -> bool:
    """Use a GPU when the GPU compose profile sets F3D_CYCLES_DEVICE (OPTIX, CUDA, HIP, ...) and Blender
    finds a matching device; otherwise fall back to the CPU silently (spec §14.5: GPU is optional)."""
    backend = os.environ.get("F3D_CYCLES_DEVICE", "").upper()
    if backend not in GPU_BACKENDS:
        return False
    try:
        prefs = bpy.context.preferences.addons["cycles"].preferences
        prefs.compute_device_type = backend
        prefs.get_devices()
        found = False
        for device in prefs.devices:
            device.use = device.type == backend
            found = found or device.use
        return found
    except Exception:
        return False


def _cycles(samples: int) -> None:
    scene = bpy.context.scene
    scene.render.engine = "CYCLES"
    scene.cycles.device = "GPU" if _gpu_device() else "CPU"
    scene.cycles.samples = samples
    scene.cycles.use_adaptive_sampling = True
    try:
        scene.cycles.use_denoising = True
        scene.cycles.denoiser = "OPENIMAGEDENOISE"
    except (AttributeError, TypeError):
        scene.cycles.use_denoising = False


def _eevee(samples: int) -> None:
    scene = bpy.context.scene
    for engine in ("BLENDER_EEVEE_NEXT", "BLENDER_EEVEE"):
        try:
            scene.render.engine = engine
            break
        except TypeError:
            continue
    scene.eevee.taa_render_samples = samples


def _output(path: str, width: int, height: int, transparent: bool) -> None:
    scene = bpy.context.scene
    scene.render.resolution_x = width
    scene.render.resolution_y = height
    scene.render.resolution_percentage = 100
    scene.render.film_transparent = transparent
    scene.render.image_settings.file_format = "PNG"
    scene.render.image_settings.color_mode = "RGBA" if transparent else "RGB"
    scene.render.filepath = path


def render_thumbnail(path: str, size: int, samples: int = 24) -> None:
    scene = bpy.context.scene
    scene.camera = _auto_camera(1.0)
    if not _user_lights():
        _add_light("__thumb_key", "SUN", 3.0, rotation=(math.radians(50), 0, math.radians(35)))
    _ensure_world()
    _cycles(samples)
    _output(path, size, size, False)
    bpy.ops.render.render(write_still=True)


class RenderError(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code, self.message = code, message


def render_image(path: str, settings: dict, profiles: dict) -> dict:
    """Render a version. Returns metadata about what was used (camera, lights, engine)."""
    scene = bpy.context.scene
    width, height = int(settings.get("width", 1280)), int(settings.get("height", 720))
    camera_id = settings.get("camera_object_id")
    if camera_id:
        cam = next((o for o in bpy.data.objects if o.get(PROP_OBJECT_ID) == camera_id), None)
        if cam is None or cam.type != "CAMERA":
            raise RenderError("unknown_camera", f"camera '{camera_id}' does not exist in this version")
        scene.camera, camera_used = cam, camera_id
    elif scene.camera is not None and PROP_OBJECT_ID in scene.camera:
        camera_used = scene.camera[PROP_OBJECT_ID]
    else:
        scene.camera, camera_used = _auto_camera(width / height), None
    lights = _user_lights()
    if not lights:
        center, radius = _scene_bounds()
        _studio_rig(center, radius)
    _ensure_world()
    profile = profiles[settings.get("quality", "standard")]
    engine = settings.get("engine", "cycles")
    if engine == "eevee":
        _eevee(profile["eevee_samples"])
    else:
        _cycles(profile["cycles_samples"])
    _output(path, width, height, bool(settings.get("transparent_background")))
    bpy.ops.render.render(write_still=True)
    return {
        "engine": engine,
        "quality": settings.get("quality", "standard"),
        "width": width,
        "height": height,
        "camera_object_id": camera_used,
        "scene_lights": len(lights),
        "samples": profile["eevee_samples" if engine == "eevee" else "cycles_samples"],
    }
