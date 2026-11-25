"""Primitive creation via bmesh/data API (no bpy.ops, so no UI-context dependence).

Dimensions are baked into mesh data (object scale stays 1) so bevel widths stay uniform."""

from __future__ import annotations

import math

import bmesh
import bpy
from mathutils import Euler, Matrix, Vector

from .context import (
    PROP_COMPONENT,
    PROP_CREATED,
    PROP_MODIFIED,
    PROP_OBJECT_ID,
    PROP_PARENT,
    PROP_PROJECT_ID,
    PROP_VISIBLE,
    OpError,
    RuntimeContext,
)
from .material_manager import assign_material_to


def _bm_cube(p: dict) -> bmesh.types.BMesh:
    bm = bmesh.new()
    bmesh.ops.create_cube(bm, size=1.0)
    sx, sy, sz = p.get("dimensions", (1.0, 1.0, 1.0))
    bmesh.ops.scale(bm, vec=Vector((sx, sy, sz)), verts=bm.verts)
    return bm


def _bm_plane(p: dict) -> bmesh.types.BMesh:
    bm = bmesh.new()
    sx, sy = p.get("size", (1.0, 1.0))
    bmesh.ops.create_grid(bm, x_segments=1, y_segments=1, size=0.5)
    bmesh.ops.scale(bm, vec=Vector((sx, sy, 1.0)), verts=bm.verts)
    return bm


def _bm_sphere(p: dict) -> bmesh.types.BMesh:
    bm = bmesh.new()
    bmesh.ops.create_uvsphere(
        bm,
        u_segments=int(p.get("segments", 32)),
        v_segments=int(p.get("rings", 16)),
        radius=float(p.get("radius", 0.5)),
    )
    return bm


def _bm_cone(radius_bottom: float, radius_top: float, depth: float, vertices: int) -> bmesh.types.BMesh:
    bm = bmesh.new()
    bmesh.ops.create_cone(
        bm, cap_ends=True, cap_tris=False, segments=vertices, radius1=radius_bottom, radius2=radius_top, depth=depth
    )
    return bm


def _bm_cylinder(p: dict) -> bmesh.types.BMesh:
    r = float(p.get("radius", 0.5))
    return _bm_cone(r, r, float(p.get("depth", 1.0)), int(p.get("vertices", 32)))


def _bm_cone_op(p: dict) -> bmesh.types.BMesh:
    return _bm_cone(
        float(p.get("radius_bottom", 0.5)),
        float(p.get("radius_top", 0.0)),
        float(p.get("depth", 1.0)),
        int(p.get("vertices", 32)),
    )


def _bm_torus(p: dict) -> bmesh.types.BMesh:
    R = float(p.get("major_radius", 0.5))
    r = float(p.get("minor_radius", 0.1))
    nu = int(p.get("major_segments", 48))
    nv = int(p.get("minor_segments", 12))
    bm = bmesh.new()
    verts = []
    for i in range(nu):
        u = 2 * math.pi * i / nu
        for j in range(nv):
            v = 2 * math.pi * j / nv
            verts.append(
                bm.verts.new(
                    ((R + r * math.cos(v)) * math.cos(u), (R + r * math.cos(v)) * math.sin(u), r * math.sin(v))
                )
            )
    for i in range(nu):
        for j in range(nv):
            a = verts[i * nv + j]
            b = verts[((i + 1) % nu) * nv + j]
            c = verts[((i + 1) % nu) * nv + (j + 1) % nv]
            d = verts[i * nv + (j + 1) % nv]
            bm.faces.new((a, b, c, d))
    bm.normal_update()
    return bm


def _bm_circle(p: dict) -> bmesh.types.BMesh:
    bm = bmesh.new()
    bmesh.ops.create_circle(
        bm,
        cap_ends=True,
        cap_tris=p.get("fill") == "triangle_fan",
        segments=int(p.get("vertices", 32)),
        radius=float(p.get("radius", 0.5)),
    )
    return bm


def _bm_mesh(p: dict) -> bmesh.types.BMesh:
    bm = bmesh.new()
    verts = [bm.verts.new(Vector(v)) for v in p["vertices"]]
    for i, face in enumerate(p["faces"]):
        try:
            bm.faces.new([verts[j] for j in face])
        except ValueError as exc:  # duplicate face / degenerate loop
            bm.free()
            raise OpError("invalid_mesh", f"face {i} is invalid: {exc}") from None
    loose = [v for v in bm.verts if not v.link_faces]
    if loose:
        bmesh.ops.delete(bm, geom=loose, context="VERTS")
    bmesh.ops.recalc_face_normals(bm, faces=bm.faces)
    return bm


def _bm_curve(p: dict) -> bmesh.types.BMesh:
    """Build a curve with a round bevel profile, evaluate it and keep only the tube mesh."""
    curve = bpy.data.curves.new("__f3d_curve", "CURVE")
    curve.dimensions = "3D"
    curve.bevel_mode = "ROUND"
    curve.bevel_depth = float(p.get("thickness", 0.02)) / 2
    curve.bevel_resolution = int(p.get("profile_resolution", 4))
    curve.use_fill_caps = True
    curve.resolution_u = int(p.get("resolution", 12))
    points = [Vector(v) for v in p["points"]]
    if p.get("curve_type", "smooth") == "smooth":
        spline = curve.splines.new("BEZIER")
        spline.bezier_points.add(len(points) - 1)
        for bp, co in zip(spline.bezier_points, points, strict=True):
            bp.co = co
            bp.handle_left_type = bp.handle_right_type = "AUTO"
    else:
        spline = curve.splines.new("POLY")
        spline.points.add(len(points) - 1)
        for sp, co in zip(spline.points, points, strict=True):
            sp.co = (*co, 1.0)
    spline.use_cyclic_u = bool(p.get("cyclic", False))
    tmp = bpy.data.objects.new("__f3d_curve", curve)
    bpy.context.scene.collection.objects.link(tmp)
    try:
        bpy.context.view_layer.update()
        evaluated = tmp.evaluated_get(bpy.context.evaluated_depsgraph_get())
        mesh = bpy.data.meshes.new_from_object(evaluated)
    finally:
        bpy.data.objects.remove(tmp, do_unlink=True)
        bpy.data.curves.remove(curve)
    bm = bmesh.new()
    bm.from_mesh(mesh)
    bpy.data.meshes.remove(mesh)
    if not bm.faces:
        bm.free()
        raise OpError("empty_curve", "the curve produced no geometry; increase thickness")
    bmesh.ops.remove_doubles(bm, verts=bm.verts, dist=1e-6)
    return bm


BUILDERS = {
    "create_cube": _bm_cube,
    "create_plane": _bm_plane,
    "create_sphere": _bm_sphere,
    "create_cylinder": _bm_cylinder,
    "create_cone": _bm_cone_op,
    "create_torus": _bm_torus,
    "create_circle": _bm_circle,
    "create_mesh": _bm_mesh,
    "create_curve": _bm_curve,
}


def set_parent(
    ctx: RuntimeContext, obj: bpy.types.Object, parent: bpy.types.Object | None, keep_transform: bool = True
) -> None:
    ctx.update()
    world = obj.matrix_world.copy()
    obj.parent = parent
    if parent is None:
        obj.matrix_parent_inverse = Matrix.Identity(4)
        if keep_transform:
            obj.matrix_world = world
        if "app_parent_id" in obj:
            del obj[PROP_PARENT]
    else:
        obj.matrix_parent_inverse = Matrix.Identity(4)
        if keep_transform:
            obj.matrix_world = world
        obj[PROP_PARENT] = parent[PROP_OBJECT_ID]
    ctx.update()


def link_to_collection(ctx: RuntimeContext, obj: bpy.types.Object, collection_id: str | None) -> None:
    target = ctx.collection(collection_id) if collection_id else ctx.scene.collection
    for col in list(obj.users_collection):
        col.objects.unlink(obj)
    target.objects.link(obj)


def tag_new_object(ctx: RuntimeContext, obj: bpy.types.Object, object_id: str, component: str | None) -> None:
    obj[PROP_OBJECT_ID] = object_id
    obj[PROP_PROJECT_ID] = ctx.project_id
    obj[PROP_COMPONENT] = component or ""
    obj[PROP_CREATED] = ctx.version
    obj[PROP_MODIFIED] = ctx.version
    obj[PROP_VISIBLE] = True
    ctx.objects[object_id] = obj
    ctx.touched.add(object_id)


def create_primitive(ctx: RuntimeContext, op: dict) -> None:
    object_id = op["target_object_id"]
    if object_id in ctx.objects:
        raise OpError("object_exists", f"object '{object_id}' already exists")
    p = op["parameters"]
    bm = BUILDERS[op["operation_type"]](p)
    mesh = bpy.data.meshes.new(p["name"])
    bm.to_mesh(mesh)
    bm.free()
    mesh.validate()
    mesh.update()

    obj = bpy.data.objects.new(p["name"], mesh)
    link_to_collection(ctx, obj, p.get("collection_id"))
    obj.rotation_mode = "XYZ"
    obj.location = Vector(p.get("location", (0.0, 0.0, 0.0)))
    obj.rotation_euler = Euler([math.radians(a) for a in p.get("rotation", (0.0, 0.0, 0.0))], "XYZ")
    component = (op.get("metadata") or {}).get("component")
    tag_new_object(ctx, obj, object_id, component if isinstance(component, str) else None)

    if p.get("material_id"):
        assign_material_to(obj, ctx.material(p["material_id"]))
    # Locations are world-space: parent with keep_transform.
    if p.get("parent_id"):
        set_parent(ctx, obj, ctx.obj(p["parent_id"]), keep_transform=True)


def duplicate_object(ctx: RuntimeContext, op: dict) -> None:
    src = ctx.obj(op["target_object_id"])
    p = op["parameters"]
    new_id = p["new_object_id"]
    if new_id in ctx.objects:
        raise OpError("object_exists", f"object '{new_id}' already exists")
    ctx.update()
    dup = src.copy()
    if src.data is not None:
        dup.data = src.data.copy()
    for col in src.users_collection:
        col.objects.link(dup)
    dup.name = p["name"]
    if dup.data is not None:
        dup.data.name = p["name"]
    tag_new_object(ctx, dup, new_id, src.get(PROP_COMPONENT) or None)
    if src.parent is not None:
        dup[PROP_PARENT] = src.parent[PROP_OBJECT_ID]
    ctx.update()
    dup.matrix_world.translation = src.matrix_world.translation + Vector(p.get("offset", (0.0, 0.0, 0.0)))
    ctx.update()


def _descendants(obj: bpy.types.Object) -> list[bpy.types.Object]:
    return list(obj.children_recursive)


def delete_object(ctx: RuntimeContext, op: dict) -> None:
    obj = ctx.obj(op["target_object_id"])
    children = _descendants(obj)
    if children and not op.get("parameters", {}).get("recursive", False):
        raise OpError("has_children", f"object '{op['target_object_id']}' has children; set recursive=true")
    for o in [*children, obj]:
        oid = o.get(PROP_OBJECT_ID)
        data = o.data
        bpy.data.objects.remove(o, do_unlink=True)
        if data is not None and data.users == 0 and isinstance(data, bpy.types.Mesh):
            bpy.data.meshes.remove(data)
        if oid:
            ctx.objects.pop(oid, None)
            ctx.touched.discard(oid)


def rename_object(ctx: RuntimeContext, op: dict) -> None:
    obj = ctx.obj(op["target_object_id"])
    name = op["parameters"]["name"]
    obj.name = name
    if obj.data is not None and obj.data.users == 1:
        obj.data.name = name
    ctx.mark_modified(obj)


def parent_object(ctx: RuntimeContext, op: dict) -> None:
    obj = ctx.obj(op["target_object_id"])
    parent = ctx.obj(op["parameters"]["parent_id"])
    if parent == obj or parent in obj.children_recursive:
        raise OpError("hierarchy_cycle", "parenting would create a cycle")
    set_parent(ctx, obj, parent, op["parameters"].get("keep_transform", True))
    ctx.mark_modified(obj)


def unparent_object(ctx: RuntimeContext, op: dict) -> None:
    obj = ctx.obj(op["target_object_id"])
    set_parent(ctx, obj, None, op.get("parameters", {}).get("keep_transform", True))
    ctx.mark_modified(obj)


def set_visibility(ctx: RuntimeContext, op: dict) -> None:
    obj = ctx.obj(op["target_object_id"])
    visible = bool(op["parameters"]["visible"])
    obj.hide_viewport = not visible
    obj.hide_render = not visible
    obj[PROP_VISIBLE] = visible
    ctx.mark_modified(obj)
