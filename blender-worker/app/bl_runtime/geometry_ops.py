"""Destructive mesh edits: extrude, inset, booleans, join/separate, apply transform, set origin.

All work on mesh data through bmesh / the data API (no edit-mode operators). Shared mesh data is
copied first so an edit never leaks into another object."""

from __future__ import annotations

import bmesh
import bpy
from mathutils import Matrix, Vector

from .context import PROP_COMPONENT, PROP_OBJECT_ID, PROP_PARENT, OpError, RuntimeContext
from .object_factory import set_parent, tag_new_object

SIDES = {
    "top": Vector((0, 0, 1)),
    "bottom": Vector((0, 0, -1)),
    "front": Vector((0, -1, 0)),
    "back": Vector((0, 1, 0)),
    "right": Vector((1, 0, 0)),
    "left": Vector((-1, 0, 0)),
}


# --------------------------------------------------------------------------- helpers
def mesh_obj(ctx: RuntimeContext, object_id: str, op_type: str) -> bpy.types.Object:
    obj = ctx.obj(object_id)
    if obj.type != "MESH":
        raise OpError("not_a_mesh", f"{op_type} requires a mesh; '{obj.name}' is {obj.type}")
    return obj


def own_mesh(obj: bpy.types.Object) -> bpy.types.Mesh:
    if obj.data.users > 1:
        obj.data = obj.data.copy()
    return obj.data


class keep_children_in_place:
    """Changing a parent's transform must not move its children in world space."""

    def __init__(self, ctx: RuntimeContext, obj: bpy.types.Object) -> None:
        self.ctx, self.obj = ctx, obj

    def __enter__(self) -> None:
        self.ctx.update()
        self.saved = [(c, c.matrix_world.copy()) for c in self.obj.children]

    def __exit__(self, *exc) -> None:
        self.ctx.update()
        for child, world in self.saved:
            child.matrix_world = world
        self.ctx.update()


def _select_faces(bm: bmesh.types.BMesh, side: str) -> list:
    if side == "all":
        return list(bm.faces)
    axis = SIDES[side]
    bm.normal_update()
    return [f for f in bm.faces if f.normal.dot(axis) > 0.7]


def _remove_mesh_if_unused(mesh: bpy.types.Mesh | None) -> None:
    if mesh is not None and mesh.users == 0:
        bpy.data.meshes.remove(mesh)


def delete_tracked(ctx: RuntimeContext, obj: bpy.types.Object) -> None:
    oid = obj.get(PROP_OBJECT_ID)
    data = obj.data
    bpy.data.objects.remove(obj, do_unlink=True)
    if isinstance(data, bpy.types.Mesh):
        _remove_mesh_if_unused(data)
    if oid:
        ctx.objects.pop(oid, None)
        ctx.touched.discard(oid)


# --------------------------------------------------------------------------- extrude / inset
def extrude(ctx: RuntimeContext, op: dict) -> None:
    obj = mesh_obj(ctx, op["target_object_id"], "extrude")
    p = op["parameters"]
    side, distance = p.get("side", "top"), float(p["distance"])
    mesh = own_mesh(obj)
    bm = bmesh.new()
    bm.from_mesh(mesh)
    faces = _select_faces(bm, side)
    if not faces:
        bm.free()
        raise OpError("no_faces", f"'{obj.name}' has no faces facing {side}")
    # A flat shape (every face selected, e.g. a plane or disc) keeps its original faces as the
    # bottom cap so extruding produces a closed solid.
    flat = len(faces) == len(bm.faces)
    ret = bmesh.ops.extrude_face_region(bm, geom=faces, use_keep_orig=flat)
    verts = [e for e in ret["geom"] if isinstance(e, bmesh.types.BMVert)]
    bmesh.ops.translate(bm, vec=SIDES[side] * distance, verts=verts)
    if flat:
        bmesh.ops.reverse_faces(bm, faces=faces)
    bmesh.ops.recalc_face_normals(bm, faces=bm.faces)
    bm.to_mesh(mesh)
    bm.free()
    mesh.update()
    ctx.update()
    ctx.mark_modified(obj)


def inset(ctx: RuntimeContext, op: dict) -> None:
    obj = mesh_obj(ctx, op["target_object_id"], "inset")
    p = op.get("parameters") or {}
    side = p.get("side", "top")
    mesh = own_mesh(obj)
    bm = bmesh.new()
    bm.from_mesh(mesh)
    faces = _select_faces(bm, side)
    if not faces:
        bm.free()
        raise OpError("no_faces", f"'{obj.name}' has no faces facing {side}")
    kwargs = {"faces": faces, "thickness": float(p.get("thickness", 0.02)), "depth": float(p.get("depth", 0.0))}
    if side == "all":
        bmesh.ops.inset_individual(bm, use_even_offset=True, **kwargs)
    else:
        bmesh.ops.inset_region(bm, use_even_offset=True, **kwargs)
    bm.to_mesh(mesh)
    bm.free()
    mesh.update()
    ctx.update()
    ctx.mark_modified(obj)


# --------------------------------------------------------------------------- booleans
_BOOL_OPS = {"boolean_union": "UNION", "boolean_difference": "DIFFERENCE", "boolean_intersect": "INTERSECT"}


def boolean(ctx: RuntimeContext, op: dict) -> None:
    t = op["operation_type"]
    obj = mesh_obj(ctx, op["target_object_id"], t)
    p = op["parameters"]
    operand = mesh_obj(ctx, p["operand_id"], t)
    if operand == obj:
        raise OpError("invalid_operand", "an object cannot be combined with itself")
    ctx.update()
    # Apply only the boolean: the target's other (non-destructive) modifiers stay live.
    states = [(m, m.show_viewport) for m in obj.modifiers]
    for m, _ in states:
        m.show_viewport = False
    mod = obj.modifiers.new("__f3d_boolean", "BOOLEAN")
    mod.operation = _BOOL_OPS[t]
    mod.object = operand
    if p.get("solver", "exact") == "exact":
        mod.solver = "EXACT"
    else:
        try:
            mod.solver = "FAST"
        except TypeError:
            mod.solver = "FLOAT"  # renamed in Blender 4.2+
    if hasattr(mod, "material_mode"):
        mod.material_mode = "TRANSFER" if t == "boolean_union" else "INDEX"
    operand_hidden = operand.hide_viewport
    operand.hide_viewport = False
    try:
        ctx.update()
        evaluated = obj.evaluated_get(bpy.context.evaluated_depsgraph_get())
        result = bpy.data.meshes.new_from_object(evaluated)
    finally:
        obj.modifiers.remove(mod)
        for m, shown in states:
            m.show_viewport = shown
        operand.hide_viewport = operand_hidden
    if len(result.polygons) == 0:
        bpy.data.meshes.remove(result)
        raise OpError("boolean_empty", f"{t} of '{obj.name}' and '{operand.name}' leaves no geometry")
    old = obj.data
    result.name = old.name
    obj.data = result
    _remove_mesh_if_unused(old)
    if not p.get("keep_operand", False):
        if operand.children:
            raise OpError("has_children", f"operand '{operand.name}' has children; set keep_operand=true")
        delete_tracked(ctx, operand)
    ctx.update()
    ctx.mark_modified(obj)


# --------------------------------------------------------------------------- join / separate
def _baked_mesh(ctx: RuntimeContext, obj: bpy.types.Object) -> bpy.types.Mesh:
    """The object's final shape (modifiers applied) as a new mesh in its own object space."""
    ctx.update()
    return bpy.data.meshes.new_from_object(obj.evaluated_get(bpy.context.evaluated_depsgraph_get()))


def join_objects(ctx: RuntimeContext, op: dict) -> None:
    target = mesh_obj(ctx, op["target_object_id"], "join_objects")
    others = [mesh_obj(ctx, oid, "join_objects") for oid in op["parameters"]["object_ids"]]
    if target in others:
        raise OpError("invalid_operand", "object_ids must not include the target")
    for o in others:
        if target in o.children_recursive:
            raise OpError("invalid_operand", f"'{o.name}' is a parent of '{target.name}'")
    mesh = own_mesh(target)
    ctx.update()
    to_local = target.matrix_world.inverted()
    bm = bmesh.new()
    bm.from_mesh(mesh)
    slots = [m for m in mesh.materials]
    for o in others:
        piece = _baked_mesh(ctx, o)
        piece.transform(to_local @ o.matrix_world)
        if (to_local @ o.matrix_world).determinant() < 0:
            piece.flip_normals()
        remap = []
        for mat in piece.materials:
            if mat is not None and mat not in slots:
                slots.append(mat)
            remap.append(slots.index(mat) if mat is not None else 0)
        if remap:
            for poly in piece.polygons:
                poly.material_index = remap[poly.material_index] if poly.material_index < len(remap) else 0
        bm.from_mesh(piece)  # appends to the existing geometry
        bpy.data.meshes.remove(piece)
        for child in list(o.children):
            set_parent(ctx, child, target, keep_transform=True)
            ctx.mark_modified(child)
    bm.to_mesh(mesh)
    bm.free()
    mesh.materials.clear()
    for mat in slots:
        mesh.materials.append(mat)
    mesh.update()
    for o in others:
        delete_tracked(ctx, o)
    ctx.update()
    ctx.mark_modified(target)


def _groups(mesh: bpy.types.Mesh, mode: str) -> list[list[int]]:
    """Polygon index groups: connected components, or one group per material slot."""
    if mode == "material":
        by_mat: dict[int, list[int]] = {}
        for poly in mesh.polygons:
            by_mat.setdefault(poly.material_index, []).append(poly.index)
        return [by_mat[k] for k in sorted(by_mat)]
    parent = list(range(len(mesh.vertices)))

    def find(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    for edge in mesh.edges:
        a, b = find(edge.vertices[0]), find(edge.vertices[1])
        if a != b:
            parent[a] = b
    by_root: dict[int, list[int]] = {}
    for poly in mesh.polygons:
        by_root.setdefault(find(poly.vertices[0]), []).append(poly.index)
    return list(by_root.values())


def _part_mesh(src: bpy.types.Mesh, polys: list[int], name: str) -> bpy.types.Mesh:
    remap: dict[int, int] = {}
    coords, faces, mats, smooth, uvs = [], [], [], [], []
    uv_layer = src.uv_layers.active
    for pi in polys:
        poly = src.polygons[pi]
        face = []
        for li in poly.loop_indices:
            vi = src.loops[li].vertex_index
            if vi not in remap:
                remap[vi] = len(coords)
                coords.append(src.vertices[vi].co.copy())
            face.append(remap[vi])
            if uv_layer is not None:
                uvs.append(uv_layer.data[li].uv.copy())
        faces.append(face)
        mats.append(poly.material_index)
        smooth.append(poly.use_smooth)
    used = sorted(set(mats))
    mesh = bpy.data.meshes.new(name)
    mesh.from_pydata(coords, [], faces)
    for slot in used:
        mesh.materials.append(src.materials[slot] if slot < len(src.materials) else None)
    for poly, m, sm in zip(mesh.polygons, mats, smooth, strict=True):
        poly.material_index = used.index(m)
        poly.use_smooth = sm
    if uv_layer is not None:
        layer = mesh.uv_layers.new(name=uv_layer.name)
        for loop_uv, uv in zip(layer.data, uvs, strict=True):
            loop_uv.uv = uv
    mesh.validate()
    mesh.update()
    return mesh


def separate_objects(ctx: RuntimeContext, op: dict) -> None:
    obj = mesh_obj(ctx, op["target_object_id"], "separate_objects")
    p = op["parameters"]
    new_ids = list(p["new_object_ids"])
    for nid in new_ids:
        if nid in ctx.objects:
            raise OpError("object_exists", f"object '{nid}' already exists")
    src = obj.data
    groups = _groups(src, p.get("mode", "loose_parts"))
    if len(groups) - 1 != len(new_ids):
        raise OpError(
            "separate_mismatch",
            f"'{obj.name}' splits into {len(groups)} parts; provide {max(len(groups) - 1, 0)} new_object_ids",
        )

    def size(g: list[int]) -> int:
        return len({v for pi in g for v in src.polygons[pi].vertices})

    def centre(g: list[int]) -> tuple[float, float, float]:
        pts = [src.vertices[v].co for pi in g for v in src.polygons[pi].vertices]
        c = sum(pts, Vector()) / len(pts)
        return (round(c.x, 4), round(c.y, 4), round(c.z, 4))

    groups.sort(key=size, reverse=True)
    keep, rest = groups[0], sorted(groups[1:], key=centre)
    names = p.get("names") or [f"{obj.name} {i + 2}" for i in range(len(rest))]
    ctx.update()
    for nid, name, g in zip(new_ids, names, rest, strict=True):
        part = bpy.data.objects.new(name, _part_mesh(src, g, name))
        for col in obj.users_collection:
            col.objects.link(part)
        tag_new_object(ctx, part, nid, obj.get(PROP_COMPONENT) or None)
        part.matrix_world = obj.matrix_world.copy()
        if obj.parent is not None:
            set_parent(ctx, part, obj.parent, keep_transform=True)
            part[PROP_PARENT] = obj.parent[PROP_OBJECT_ID]
    kept = _part_mesh(src, keep, src.name)
    obj.data = kept
    _remove_mesh_if_unused(src)
    ctx.update()
    ctx.mark_modified(obj)


# --------------------------------------------------------------------------- transforms baked into the mesh
def apply_transform(ctx: RuntimeContext, op: dict) -> None:
    obj = mesh_obj(ctx, op["target_object_id"], "apply_transform")
    p = op.get("parameters") or {}
    loc, rot, sca = obj.matrix_basis.decompose()
    L = Matrix.Translation(loc)
    R = rot.to_matrix().to_4x4()
    S = Matrix.Diagonal((*sca, 1.0))
    baked = (L if p.get("location") else Matrix.Identity(4)) @ (R if p.get("rotation", True) else Matrix.Identity(4))
    baked = baked @ (S if p.get("scale", True) else Matrix.Identity(4))
    kept = (Matrix.Identity(4) if p.get("location") else L) @ (Matrix.Identity(4) if p.get("rotation", True) else R)
    kept = kept @ (Matrix.Identity(4) if p.get("scale", True) else S)
    with keep_children_in_place(ctx, obj):
        mesh = own_mesh(obj)
        mesh.transform(baked)
        if baked.determinant() < 0:
            mesh.flip_normals()
        mesh.update()
        obj.matrix_basis = kept
    ctx.mark_modified(obj)


def set_origin(ctx: RuntimeContext, op: dict) -> None:
    obj = mesh_obj(ctx, op["target_object_id"], "set_origin")
    p = op.get("parameters") or {}
    mode = p.get("mode", "geometry_center")
    mesh = obj.data
    if len(mesh.vertices) == 0:
        raise OpError("empty_mesh", f"object '{obj.name}' has no geometry")
    ctx.update()
    lo = Vector([min(v.co[i] for v in mesh.vertices) for i in range(3)])
    hi = Vector([max(v.co[i] for v in mesh.vertices) for i in range(3)])
    if mode == "geometry_center":
        local = (lo + hi) / 2
    elif mode == "bounds_bottom":
        local = Vector(((lo.x + hi.x) / 2, (lo.y + hi.y) / 2, lo.z))
    else:
        world = Vector((0, 0, 0)) if mode == "world_origin" else Vector(p["point"])
        local = obj.matrix_world.inverted() @ world
    with keep_children_in_place(ctx, obj):
        mesh = own_mesh(obj)
        mesh.transform(Matrix.Translation(-local))
        mesh.update()
        obj.matrix_world = obj.matrix_world @ Matrix.Translation(local)
    ctx.mark_modified(obj)
