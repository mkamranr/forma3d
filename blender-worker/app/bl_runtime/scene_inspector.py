"""Deterministic scene inspection → inventory dict matching forma3d_shared SceneInventory."""

from __future__ import annotations

import math

import bpy

from .context import (
    PROP_BASE_COLOR,
    PROP_COLLECTION_ID,
    PROP_COMPONENT,
    PROP_CREATED,
    PROP_MATERIAL_ID,
    PROP_MODIFIED,
    PROP_OBJECT_ID,
    PROP_VISIBLE,
)

INVENTORY_SCHEMA_VERSION = "1.0"


def _r(v: float) -> float:
    out = round(float(v), 6)
    return 0.0 if out == 0 else out  # normalise -0.0


def _vec(v) -> list[float]:
    return [_r(x) for x in v]


def _modifier_params(mod: bpy.types.Modifier) -> dict:
    if mod.type == "BEVEL":
        return {
            "width": _r(mod.width),
            "segments": int(mod.segments),
            "limit_method": mod.limit_method.lower(),
            "angle": _r(math.degrees(mod.angle_limit)),
        }
    if mod.type == "SOLIDIFY":
        return {"thickness": _r(mod.thickness), "offset": _r(mod.offset)}
    return {}


def _mesh_stats(obj: bpy.types.Object, depsgraph) -> dict:
    if obj.type != "MESH":
        return {"vertices": 0, "edges": 0, "faces": 0, "triangles": 0}
    evaluated = obj.evaluated_get(depsgraph)
    mesh = evaluated.to_mesh()
    try:
        mesh.calc_loop_triangles()
        return {
            "vertices": len(mesh.vertices),
            "edges": len(mesh.edges),
            "faces": len(mesh.polygons),
            "triangles": len(mesh.loop_triangles),
        }
    finally:
        evaluated.to_mesh_clear()


def _collection_id(obj: bpy.types.Object) -> str | None:
    for col in obj.users_collection:
        if PROP_COLLECTION_ID in col:
            return col[PROP_COLLECTION_ID]
    return None


def _parent_collection_id(col: bpy.types.Collection) -> str | None:
    for parent in bpy.data.collections:
        if col.name in parent.children and PROP_COLLECTION_ID in parent:
            return parent[PROP_COLLECTION_ID]
    return None


def inspect_scene() -> dict:
    bpy.context.view_layer.update()
    depsgraph = bpy.context.evaluated_depsgraph_get()
    objects = []
    for obj in sorted((o for o in bpy.data.objects if PROP_OBJECT_ID in o), key=lambda o: o[PROP_OBJECT_ID]):
        materials = []
        if obj.data is not None and hasattr(obj.data, "materials"):
            materials = [m[PROP_MATERIAL_ID] for m in obj.data.materials if m is not None and PROP_MATERIAL_ID in m]
        objects.append(
            {
                "object_id": obj[PROP_OBJECT_ID],
                "name": obj.name,
                "type": obj.type,
                "component_type": obj.get(PROP_COMPONENT) or None,
                "parent_id": obj.parent[PROP_OBJECT_ID]
                if obj.parent is not None and PROP_OBJECT_ID in obj.parent
                else None,
                "collection_id": _collection_id(obj),
                "location": _vec(obj.matrix_world.translation),
                "rotation": [_r(math.degrees(a)) for a in obj.rotation_euler],
                "scale": _vec(obj.scale),
                "dimensions": _vec(obj.dimensions),
                "visible": bool(obj.get(PROP_VISIBLE, True)),
                "material_ids": materials,
                "modifiers": [{"name": m.name, "type": m.type, "params": _modifier_params(m)} for m in obj.modifiers],
                "mesh": _mesh_stats(obj, depsgraph),
                "created_version": obj.get(PROP_CREATED),
                "last_modified_version": obj.get(PROP_MODIFIED),
            }
        )
    materials = []
    for mat in sorted((m for m in bpy.data.materials if PROP_MATERIAL_ID in m), key=lambda m: m[PROP_MATERIAL_ID]):
        bsdf = mat.node_tree.nodes.get("Principled BSDF") if mat.node_tree else None
        materials.append(
            {
                "material_id": mat[PROP_MATERIAL_ID],
                "name": mat.name,
                "base_color": mat.get(PROP_BASE_COLOR, "#CCCCCC"),
                "metallic": _r(bsdf.inputs["Metallic"].default_value) if bsdf else 0.0,
                "roughness": _r(bsdf.inputs["Roughness"].default_value) if bsdf else 0.5,
                "alpha": _r(bsdf.inputs["Alpha"].default_value) if bsdf else 1.0,
            }
        )
    collections = [
        {"collection_id": c[PROP_COLLECTION_ID], "name": c.name, "parent_id": _parent_collection_id(c)}
        for c in sorted(
            (c for c in bpy.data.collections if PROP_COLLECTION_ID in c), key=lambda c: c[PROP_COLLECTION_ID]
        )
    ]
    return {
        "schema_version": INVENTORY_SCHEMA_VERSION,
        "units": bpy.context.scene.unit_settings.length_unit,
        "objects": objects,
        "materials": materials,
        "collections": collections,
    }
