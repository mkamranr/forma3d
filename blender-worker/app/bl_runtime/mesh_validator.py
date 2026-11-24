"""Structural mesh checks run after execution. Errors fail the job; warnings are reported."""

from __future__ import annotations

import math

import bpy

from .context import PROP_OBJECT_ID


def validate_meshes(max_vertices_per_object: int, max_scene_vertices: int) -> tuple[list[dict], list[dict]]:
    errors: list[dict] = []
    warnings: list[dict] = []
    depsgraph = bpy.context.evaluated_depsgraph_get()
    total = 0
    for obj in bpy.data.objects:
        if PROP_OBJECT_ID not in obj or obj.type != "MESH":
            continue
        oid = obj[PROP_OBJECT_ID]
        base = obj.data
        if len(base.vertices) == 0 or len(base.polygons) == 0:
            errors.append({"object_id": oid, "code": "empty_mesh", "message": "mesh has no faces"})
            continue
        if any(not math.isfinite(c) for v in base.vertices for c in v.co):
            errors.append(
                {"object_id": oid, "code": "invalid_coordinates", "message": "mesh has non-finite vertex coordinates"}
            )
            continue
        if any(not math.isfinite(c) for row in obj.matrix_world for c in row):
            errors.append({"object_id": oid, "code": "invalid_transform", "message": "object transform is not finite"})
            continue
        if any(abs(s) < 1e-6 for s in obj.scale):
            errors.append({"object_id": oid, "code": "zero_scale", "message": "object has zero scale on an axis"})
        evaluated = obj.evaluated_get(depsgraph)
        mesh = evaluated.to_mesh()
        try:
            count = len(mesh.vertices)
            degenerate = sum(1 for p in mesh.polygons if p.area < 1e-12)
        finally:
            evaluated.to_mesh_clear()
        total += count
        if count > max_vertices_per_object:
            errors.append(
                {
                    "object_id": oid,
                    "code": "too_many_vertices",
                    "message": f"{count} vertices exceeds {max_vertices_per_object}",
                }
            )
        if degenerate:
            warnings.append({"object_id": oid, "code": "degenerate_faces", "message": f"{degenerate} zero-area faces"})
    if total > max_scene_vertices:
        errors.append(
            {
                "object_id": None,
                "code": "scene_too_complex",
                "message": f"{total} vertices exceeds {max_scene_vertices}",
            }
        )
    return errors, warnings
