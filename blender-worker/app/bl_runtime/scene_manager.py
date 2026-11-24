"""Scene initialisation and loading."""

from __future__ import annotations

import bpy


def init_empty_scene() -> None:
    """Start from factory settings and remove the default cube/camera/light."""
    for obj in list(bpy.data.objects):
        bpy.data.objects.remove(obj, do_unlink=True)
    for block in (bpy.data.meshes, bpy.data.materials, bpy.data.cameras, bpy.data.lights):
        for item in list(block):
            if item.users == 0:
                block.remove(item)
    scene = bpy.context.scene
    scene.unit_settings.system = "METRIC"
    scene.unit_settings.scale_length = 1.0
    scene.unit_settings.length_unit = "METERS"


def load_blend(path: str) -> None:
    bpy.ops.wm.open_mainfile(filepath=path, load_ui=False)
