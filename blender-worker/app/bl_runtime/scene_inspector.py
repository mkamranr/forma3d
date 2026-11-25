"""Deterministic scene inspection → inventory dict matching forma3d_shared SceneInventory."""

from __future__ import annotations

import json
import math

import bpy

from .context import (
    PROP_BASE_COLOR,
    PROP_COLLECTION_ID,
    PROP_COMPONENT,
    PROP_CREATED,
    PROP_EMISSION_COLOR,
    PROP_LIGHT_COLOR,
    PROP_MATERIAL_ID,
    PROP_MODIFIED,
    PROP_OBJECT_ID,
    PROP_TEXTURE,
    PROP_VISIBLE,
    PROP_WORLD_COLOR,
    PROP_WORLD_STRENGTH,
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
    if mod.type == "SUBSURF":
        return {"levels": int(mod.levels), "smooth": mod.subdivision_type == "CATMULL_CLARK"}
    if mod.type == "MIRROR":
        axes = "".join(a for i, a in enumerate("xyz") if mod.use_axis[i])
        return {"axes": axes, "merge": bool(mod.use_mirror_merge), "bisect": any(mod.use_bisect_axis)}
    if mod.type == "ARRAY":
        off = mod.constant_offset_displace
        return {"count": int(mod.count), "offset_x": _r(off[0]), "offset_y": _r(off[1]), "offset_z": _r(off[2])}
    if mod.type == "REMESH":
        return {"voxel_size": _r(mod.voxel_size)}
    if mod.type == "DECIMATE":
        return {"ratio": _r(mod.ratio)}
    return {}


def _camera(obj: bpy.types.Object) -> dict | None:
    if obj.type != "CAMERA":
        return None
    return {"lens": _r(obj.data.lens), "active": bpy.context.scene.camera == obj}


def _light(obj: bpy.types.Object) -> dict | None:
    if obj.type != "LIGHT":
        return None
    light = obj.data
    if light.type == "SUN":
        radius = math.degrees(light.angle) / 10
    elif light.type == "AREA":
        radius = light.size
    else:
        radius = light.shadow_soft_size
    return {
        "light_type": light.type.lower(),
        "color": light.get(PROP_LIGHT_COLOR, "#FFFFFF"),
        "energy": _r(light.energy),
        "radius": _r(radius),
        "spot_angle": _r(math.degrees(light.spot_size)) if light.type == "SPOT" else None,
    }


def _input(bsdf, *names: str, default: float = 0.0) -> float:
    for name in names:
        sock = bsdf.inputs.get(name) if bsdf else None
        if sock is not None:
            return _r(sock.default_value)
    return default


def _world() -> dict | None:
    world = bpy.context.scene.world
    if world is None or PROP_WORLD_COLOR not in world:
        return None
    return {"color": world[PROP_WORLD_COLOR], "strength": _r(world.get(PROP_WORLD_STRENGTH, 1.0))}


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
                "camera": _camera(obj),
                "light": _light(obj),
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
                "emission_color": mat.get(PROP_EMISSION_COLOR),
                "emission_strength": _input(bsdf, "Emission Strength"),
                "transmission": _input(bsdf, "Transmission Weight", "Transmission"),
                "ior": _input(bsdf, "IOR", default=1.45),
                "texture": json.loads(mat[PROP_TEXTURE]) if PROP_TEXTURE in mat else None,
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
        "world": _world(),
    }
