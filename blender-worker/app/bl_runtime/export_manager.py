"""Saving the native .blend and exporting GLB (custom properties exported as glTF extras)."""

from __future__ import annotations

import bpy


def save_blend(path: str) -> None:
    bpy.ops.wm.save_as_mainfile(filepath=path, compress=True, copy=True, check_existing=False)


def export_glb(path: str) -> None:
    bpy.ops.export_scene.gltf(
        filepath=path,
        export_format="GLB",
        export_extras=True,  # app_object_id etc. -> node.extras -> three.js userData
        export_apply=True,  # bake bevel/solidify so the browser sees the final shape
        export_yup=True,
        export_cameras=False,
        export_lights=False,
        use_selection=False,
        use_visible=False,  # hidden objects are exported; visibility is in extras
        export_materials="EXPORT",
    )
