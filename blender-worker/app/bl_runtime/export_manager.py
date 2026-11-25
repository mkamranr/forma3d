"""Saving the native .blend and exporting interchange formats.

GLB (every version): custom properties are exported as glTF extras, so app_object_id reaches
three.js userData; cameras and lights are exported too (as glTF cameras / KHR_lights_punctual).
glTF / OBJ / STL are on-demand export jobs; multi-file formats are zipped with their textures."""

from __future__ import annotations

import os
import shutil
import zipfile

import bpy

from .context import PROP_OBJECT_ID


def save_blend(path: str) -> None:
    bpy.ops.wm.save_as_mainfile(filepath=path, compress=True, copy=True, check_existing=False)


def _gltf(path: str, fmt: str) -> None:
    bpy.ops.export_scene.gltf(
        filepath=path,
        export_format=fmt,
        export_extras=True,  # app_object_id etc. -> node.extras -> three.js userData
        export_apply=True,  # bake modifiers so viewers see the final shape
        export_yup=True,
        export_cameras=True,
        export_lights=True,
        use_selection=False,
        use_visible=False,  # hidden objects are exported; visibility is in extras
        export_materials="EXPORT",
    )


def export_glb(path: str) -> None:
    _gltf(path, "GLB")


def _zip(folder: str, zip_path: str) -> None:
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for root, _dirs, files in os.walk(folder):
            for name in sorted(files):
                full = os.path.join(root, name)
                zf.write(full, os.path.relpath(full, folder))


def _hide_hidden(include_hidden: bool) -> list[bpy.types.Object]:
    """Exclude objects the user hid (OBJ/STL have no visibility flag)."""
    if include_hidden:
        return []
    hidden = [o for o in bpy.data.objects if PROP_OBJECT_ID in o and not o.get("app_visible", True)]
    for o in hidden:
        o.hide_viewport = True
    return hidden


def _save_textures(folder: str) -> None:
    """Write packed texture images next to the OBJ so the .mtl can reference them."""
    for img in bpy.data.images:
        if img.users == 0 or img.type != "IMAGE" or not (img.packed_file or img.source == "GENERATED"):
            continue
        safe = "".join(c if c.isalnum() or c in "-_" else "_" for c in img.name)[:60] or "texture"
        target = os.path.join(folder, f"{safe}.png")
        img.filepath_raw = target
        img.file_format = "PNG"
        img.save()


def export_file(out_dir: str, fmt: str, unit_scale: float, include_hidden: bool, filename: str) -> str:
    target = os.path.join(out_dir, filename)
    _hide_hidden(include_hidden)
    if fmt == "stl":
        bpy.ops.wm.stl_export(
            filepath=target,
            ascii_format=False,
            apply_modifiers=True,
            global_scale=unit_scale,
            export_selected_objects=False,
            use_scene_unit=False,
        )
        return target
    folder = os.path.join(out_dir, "export")
    shutil.rmtree(folder, ignore_errors=True)
    os.makedirs(folder)
    if fmt == "gltf":
        _gltf(os.path.join(folder, "scene.gltf"), "GLTF_SEPARATE")
    elif fmt == "obj":
        _save_textures(folder)
        bpy.ops.wm.obj_export(
            filepath=os.path.join(folder, "scene.obj"),
            export_selected_objects=False,
            apply_modifiers=True,
            export_materials=True,
            path_mode="STRIP",  # texture file names only; the images sit next to the .mtl
            global_scale=unit_scale,
            forward_axis="NEGATIVE_Z",
            up_axis="Y",
        )
    else:
        raise ValueError(f"unsupported export format {fmt!r}")
    _zip(folder, target)
    shutil.rmtree(folder, ignore_errors=True)
    return target
