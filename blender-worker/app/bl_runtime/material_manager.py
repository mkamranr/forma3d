"""Principled-BSDF materials addressed by stable material ids."""

from __future__ import annotations

import bpy

from .context import PROP_BASE_COLOR, PROP_EMISSION_COLOR, PROP_MATERIAL_ID, OpError, RuntimeContext


def srgb_to_linear(c: float) -> float:
    return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4


def hex_to_linear_rgba(hex_color: str, alpha: float = 1.0) -> tuple[float, float, float, float]:
    h = hex_color.lstrip("#")
    r, g, b = (int(h[i : i + 2], 16) / 255.0 for i in (0, 2, 4))
    return (srgb_to_linear(r), srgb_to_linear(g), srgb_to_linear(b), alpha)


def _bsdf(mat: bpy.types.Material) -> bpy.types.Node:
    node = mat.node_tree.nodes.get("Principled BSDF") if mat.node_tree else None
    if node is None:
        raise OpError("material_corrupt", f"material '{mat.name}' has no Principled BSDF node")
    return node


EXTRA_INPUTS = {
    # DSL field -> Principled BSDF input (Blender 4.x names)
    "emission_strength": "Emission Strength",
    "transmission": "Transmission Weight",
    "ior": "IOR",
}


def _input(bsdf: bpy.types.Node, name: str):
    sock = bsdf.inputs.get(name)
    if sock is None and name == "Transmission Weight":
        sock = bsdf.inputs.get("Transmission")  # Blender < 4.0
    return sock


def _apply_extras(mat: bpy.types.Material, values: dict) -> None:
    bsdf = _bsdf(mat)
    if values.get("emission_color") is not None:
        mat[PROP_EMISSION_COLOR] = values["emission_color"].upper()
        sock = bsdf.inputs.get("Emission Color") or bsdf.inputs.get("Emission")
        sock.default_value = (*hex_to_linear_rgba(values["emission_color"])[:3], 1.0)
    for field, name in EXTRA_INPUTS.items():
        if values.get(field) is not None:
            _input(bsdf, name).default_value = float(values[field])
    strength = _input(bsdf, "Emission Strength").default_value
    if strength > 0 and PROP_EMISSION_COLOR not in mat:
        # glowing without an explicit colour: glow in the base colour
        mat[PROP_EMISSION_COLOR] = mat.get(PROP_BASE_COLOR, "#FFFFFF")
        (bsdf.inputs.get("Emission Color") or bsdf.inputs.get("Emission")).default_value = (
            *hex_to_linear_rgba(mat[PROP_EMISSION_COLOR])[:3],
            1.0,
        )


def _apply(mat: bpy.types.Material, *, base_color=None, metallic=None, roughness=None, alpha=None) -> None:
    bsdf = _bsdf(mat)
    if base_color is not None:
        mat[PROP_BASE_COLOR] = base_color.upper()
    current_alpha = float(bsdf.inputs["Alpha"].default_value)
    if alpha is not None:
        current_alpha = float(alpha)
        bsdf.inputs["Alpha"].default_value = current_alpha
        if hasattr(mat, "surface_render_method"):
            mat.surface_render_method = "BLENDED" if current_alpha < 1.0 else "DITHERED"
    if base_color is not None or alpha is not None:
        rgba = hex_to_linear_rgba(mat.get(PROP_BASE_COLOR, "#CCCCCC"), current_alpha)
        bsdf.inputs["Base Color"].default_value = (*rgba[:3], 1.0)
        mat.diffuse_color = rgba  # viewport / workbench colour
    if metallic is not None:
        bsdf.inputs["Metallic"].default_value = float(metallic)
        mat.metallic = float(metallic)
    if roughness is not None:
        bsdf.inputs["Roughness"].default_value = float(roughness)
        mat.roughness = float(roughness)


def assign_material_to(obj: bpy.types.Object, mat: bpy.types.Material) -> None:
    if obj.data is None or not hasattr(obj.data, "materials"):
        raise OpError("not_a_mesh", f"object '{obj.name}' cannot hold materials")
    obj.data.materials.clear()
    obj.data.materials.append(mat)


def create_material(ctx: RuntimeContext, op: dict) -> None:
    p = op["parameters"]
    if p["material_id"] in ctx.materials:
        raise OpError("material_exists", f"material '{p['material_id']}' already exists")
    mat = bpy.data.materials.new(p["name"])
    mat.use_nodes = True
    mat[PROP_MATERIAL_ID] = p["material_id"]
    _apply(
        mat,
        base_color=p.get("base_color", "#CCCCCC"),
        metallic=p.get("metallic", 0.0),
        roughness=p.get("roughness", 0.5),
        alpha=p.get("alpha", 1.0),
    )
    _apply_extras(mat, p)
    ctx.materials[p["material_id"]] = mat


def update_material(ctx: RuntimeContext, op: dict) -> None:
    p = op["parameters"]
    mat = ctx.material(p["material_id"])
    if p.get("name"):
        mat.name = p["name"]
    _apply(
        mat,
        base_color=p.get("base_color"),
        metallic=p.get("metallic"),
        roughness=p.get("roughness"),
        alpha=p.get("alpha"),
    )
    _apply_extras(mat, p)
    _mark_users(ctx, mat)


def set_base_color(ctx: RuntimeContext, op: dict) -> None:
    mat = ctx.material(op["parameters"]["material_id"])
    _apply(mat, base_color=op["parameters"]["base_color"])
    _mark_users(ctx, mat)


def set_metallic(ctx: RuntimeContext, op: dict) -> None:
    mat = ctx.material(op["parameters"]["material_id"])
    _apply(mat, metallic=op["parameters"]["metallic"])
    _mark_users(ctx, mat)


def set_roughness(ctx: RuntimeContext, op: dict) -> None:
    mat = ctx.material(op["parameters"]["material_id"])
    _apply(mat, roughness=op["parameters"]["roughness"])
    _mark_users(ctx, mat)


def set_transparency(ctx: RuntimeContext, op: dict) -> None:
    mat = ctx.material(op["parameters"]["material_id"])
    _apply(mat, alpha=op["parameters"]["alpha"])
    _mark_users(ctx, mat)


def assign_material(ctx: RuntimeContext, op: dict) -> None:
    obj = ctx.obj(op["target_object_id"])
    assign_material_to(obj, ctx.material(op["parameters"]["material_id"]))
    ctx.mark_modified(obj)


def _mark_users(ctx: RuntimeContext, mat: bpy.types.Material) -> None:
    """Objects whose appearance changed because a shared material changed."""
    for obj in ctx.objects.values():
        if obj.data is not None and hasattr(obj.data, "materials") and mat.name in obj.data.materials:
            ctx.mark_modified(obj)
