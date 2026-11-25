"""Colour textures. Procedural patterns are generated as tileable images (numpy) and packed into the
.blend, so they survive GLB / glTF / OBJ export exactly as rendered; uploaded images are verified by
the worker service and loaded only from the job workspace. Objects using a textured material get a
box-projected UV map sized in meters (texture density stays uniform across parts and resizes)."""

from __future__ import annotations

import json
import math
import os

import bpy
import numpy as np

from .context import PROP_BASE_COLOR, PROP_TEXTURE, OpError, RuntimeContext

SIZE = 512
NODE = "F3D Texture"


# --------------------------------------------------------------------------- tileable noise
def _value_noise(rng: np.random.Generator, cells: int, size: int = SIZE) -> np.ndarray:
    grid = rng.random((cells, cells))
    t = np.arange(size) * cells / size
    i0 = np.floor(t).astype(int)
    f = t - i0
    f = f * f * (3 - 2 * f)  # smoothstep
    i1 = (i0 + 1) % cells
    a = grid[np.ix_(i0, i0)]
    b = grid[np.ix_(i0, i1)]
    c = grid[np.ix_(i1, i0)]
    d = grid[np.ix_(i1, i1)]
    fx = f[None, :]
    fy = f[:, None]
    return (a * (1 - fx) + b * fx) * (1 - fy) + (c * (1 - fx) + d * fx) * fy


def _fbm(seed: int, base: int = 4, octaves: int = 5) -> np.ndarray:
    rng = np.random.default_rng(seed)
    out = np.zeros((SIZE, SIZE))
    amp, total = 1.0, 0.0
    for o in range(octaves):
        out += amp * _value_noise(rng, base * 2**o)
        total += amp
        amp *= 0.5
    return out / total


def _uv() -> tuple[np.ndarray, np.ndarray]:
    t = np.arange(SIZE) / SIZE
    return np.meshgrid(t, t)  # u along columns, v along rows


def pattern_mask(kind: str, seed: int = 7) -> np.ndarray:
    """0..1 mix factor between colour A (0) and colour B (1). Every pattern tiles seamlessly."""
    u, v = _uv()
    if kind == "noise":
        return _fbm(seed)
    if kind == "wood":
        n = _fbm(seed, base=2)
        rings = 0.5 + 0.5 * np.sin(2 * np.pi * (6 * v + 1.6 * n + 0.15 * np.sin(2 * np.pi * u)))
        grain = _fbm(seed + 1, base=16, octaves=3)
        return np.clip(0.75 * rings**3 + 0.25 * grain, 0, 1)
    if kind == "marble":
        n = _fbm(seed, base=3)
        veins = 1 - np.abs(np.sin(2 * np.pi * (2 * u + 2 * v + 2.5 * n)))
        return np.clip(veins**8 * 1.2, 0, 1)
    if kind == "checker":
        return ((np.floor(u * 8) + np.floor(v * 8)) % 2).astype(float)
    if kind == "brick":
        rows = 8
        row = np.floor(v * rows)
        shifted = u * 4 + 0.5 * (row % 2)
        mortar_u = np.abs(shifted - np.round(shifted)) < 0.03
        mortar_v = np.abs(v * rows - np.round(v * rows)) < 0.06
        variation = 0.25 * _fbm(seed, base=8, octaves=2)
        return np.where(mortar_u | mortar_v, 1.0, variation)
    if kind == "fabric":
        warp = 0.5 + 0.5 * np.sin(2 * np.pi * 48 * u)
        weft = 0.5 + 0.5 * np.sin(2 * np.pi * 48 * v)
        over = (np.floor(u * 48) + np.floor(v * 48)) % 2
        weave = np.where(over == 0, warp, weft)
        return np.clip(0.7 * weave + 0.3 * _fbm(seed, base=16, octaves=2), 0, 1)
    if kind == "concrete":
        speckle = (np.random.default_rng(seed).random((SIZE, SIZE)) > 0.985).astype(float)
        return np.clip(0.8 * _fbm(seed, base=8) + 0.6 * speckle, 0, 1)
    if kind == "brushed_metal":
        rng = np.random.default_rng(seed)
        lines = rng.random(SIZE)[:, None] * np.ones((1, SIZE))
        streaks = _value_noise(rng, 4)
        return np.clip(0.6 * lines + 0.4 * streaks, 0, 1)
    raise OpError("unknown_texture", f"unknown texture pattern '{kind}'")


def _hex_srgb(h: str) -> np.ndarray:
    h = h.lstrip("#")
    return np.array([int(h[i : i + 2], 16) / 255.0 for i in (0, 2, 4)])


def _darker(h: str, factor: float = 0.55) -> str:
    rgb = _hex_srgb(h) * factor
    return "#" + "".join(f"{int(round(c * 255)):02X}" for c in rgb)


def pattern_image(name: str, kind: str, color_a: str, color_b: str) -> bpy.types.Image:
    mask = pattern_mask(kind)[..., None]
    rgb = _hex_srgb(color_a) * (1 - mask) + _hex_srgb(color_b) * mask
    rgba = np.concatenate([rgb, np.ones((SIZE, SIZE, 1))], axis=2).astype(np.float32)
    img = bpy.data.images.new(name, SIZE, SIZE, alpha=False)
    img.pixels.foreach_set(rgba.ravel())  # byte images hold sRGB values
    img.pack()
    return img


def _load_upload(ctx: RuntimeContext, image_id: str, name: str) -> bpy.types.Image:
    path = ctx.textures.get(image_id)
    if not path or not os.path.isfile(path):
        raise OpError("unknown_image", f"texture image '{image_id}' is not available")
    img = bpy.data.images.load(path, check_existing=False)
    img.name = name
    img.pack()
    img.filepath = ""  # never keep a workspace path in the saved project
    return img


# --------------------------------------------------------------------------- material wiring
def texture_info(mat: bpy.types.Material) -> dict | None:
    raw = mat.get(PROP_TEXTURE)
    return json.loads(raw) if raw else None


def _clear_texture_nodes(mat: bpy.types.Material) -> None:
    node = mat.node_tree.nodes.get(NODE)
    if node is not None:
        img = node.image
        mat.node_tree.nodes.remove(node)
        if img is not None and img.users == 0:
            bpy.data.images.remove(img)


def assign_texture(ctx: RuntimeContext, op: dict) -> None:
    p = op["parameters"]
    mat = ctx.material(p["material_id"])
    bsdf = mat.node_tree.nodes.get("Principled BSDF")
    if bsdf is None:
        raise OpError("material_corrupt", f"material '{mat.name}' has no Principled BSDF node")
    _clear_texture_nodes(mat)
    name = f"{mat.name} texture"
    info = {"scale": float(p.get("scale", 1.0)), "rotation": float(p.get("rotation", 0.0))}
    if p.get("pattern"):
        base = mat.get(PROP_BASE_COLOR, "#CCCCCC")
        info.update(
            pattern=p["pattern"],
            color_a=(p.get("color_a") or base).upper(),
            color_b=(p.get("color_b") or _darker(p.get("color_a") or base)).upper(),
        )
        img = pattern_image(name, p["pattern"], info["color_a"], info["color_b"])
    else:
        info["image_id"] = p["image_id"]
        img = _load_upload(ctx, p["image_id"], name)
    node = mat.node_tree.nodes.new("ShaderNodeTexImage")
    node.name = node.label = NODE
    node.image = img
    node.location = (bsdf.location.x - 320, bsdf.location.y)
    mat.node_tree.links.new(node.outputs["Color"], bsdf.inputs["Base Color"])
    mat[PROP_TEXTURE] = json.dumps(info, sort_keys=True)
    _mark_users(ctx, mat)


def remove_texture(ctx: RuntimeContext, op: dict) -> None:
    mat = ctx.material(op["parameters"]["material_id"])
    _clear_texture_nodes(mat)
    if PROP_TEXTURE in mat:
        del mat[PROP_TEXTURE]
    _mark_users(ctx, mat)


def _mark_users(ctx: RuntimeContext, mat: bpy.types.Material) -> None:
    for obj in ctx.objects.values():
        if obj.type == "MESH" and mat.name in obj.data.materials:
            ctx.mark_modified(obj)


# --------------------------------------------------------------------------- UVs
def box_project(mesh: bpy.types.Mesh, scale: float, rotation_deg: float) -> None:
    n_loops = len(mesh.loops)
    if n_loops == 0:
        return
    co = np.empty(len(mesh.vertices) * 3)
    mesh.vertices.foreach_get("co", co)
    co = co.reshape(-1, 3)
    vidx = np.empty(n_loops, dtype=np.int64)
    mesh.loops.foreach_get("vertex_index", vidx)
    normals = np.empty(len(mesh.polygons) * 3)
    mesh.polygons.foreach_get("normal", normals)
    normals = normals.reshape(-1, 3)
    totals = np.empty(len(mesh.polygons), dtype=np.int64)
    mesh.polygons.foreach_get("loop_total", totals)
    loop_normals = np.repeat(normals, totals, axis=0)
    axis = np.argmax(np.abs(loop_normals), axis=1)
    pts = co[vidx]
    u = np.where(axis == 0, pts[:, 1], pts[:, 0])
    v = np.where(axis == 2, pts[:, 1], pts[:, 2])
    a = math.radians(rotation_deg)
    uu = (u * math.cos(a) - v * math.sin(a)) * scale
    vv = (u * math.sin(a) + v * math.cos(a)) * scale
    layer = mesh.uv_layers.get("UVMap") or mesh.uv_layers.new(name="UVMap")
    mesh.uv_layers.active = layer
    layer.data.foreach_set("uv", np.stack([uu, vv], axis=1).astype(np.float32).ravel())


def finalize_uvs(ctx: RuntimeContext) -> None:
    """(Re)project UVs for changed objects that use a textured material."""
    for oid in sorted(ctx.touched):
        obj = ctx.objects.get(oid)
        if obj is None or obj.type != "MESH":
            continue
        infos = [texture_info(m) for m in obj.data.materials if m is not None]
        info = next((i for i in infos if i), None)
        if info is None:
            continue
        if obj.data.users > 1:
            obj.data = obj.data.copy()
        box_project(obj.data, float(info.get("scale", 1.0)), float(info.get("rotation", 0.0)))
