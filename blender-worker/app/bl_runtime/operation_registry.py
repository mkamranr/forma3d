"""Explicit allowlist of DSL operation handlers. Must equal forma3d_shared ALLOWED_OPERATIONS
(enforced by tests/test_registry_parity.py)."""

from __future__ import annotations

from collections.abc import Callable

from . import camera_light_manager as cam
from . import collection_manager as col
from . import geometry_ops as geo
from . import material_manager as mat
from . import modifier_manager as mod
from . import object_factory as obj
from . import texture_manager as tex
from . import transform_manager as xf
from .context import RuntimeContext

Handler = Callable[[RuntimeContext, dict], None]

HANDLERS: dict[str, Handler] = {
    # geometry
    "create_cube": obj.create_primitive,
    "create_sphere": obj.create_primitive,
    "create_cylinder": obj.create_primitive,
    "create_cone": obj.create_primitive,
    "create_torus": obj.create_primitive,
    "create_plane": obj.create_primitive,
    "create_circle": obj.create_primitive,
    "create_mesh": obj.create_primitive,
    "create_curve": obj.create_primitive,
    "extrude": geo.extrude,
    "inset": geo.inset,
    "boolean_union": geo.boolean,
    "boolean_difference": geo.boolean,
    "boolean_intersect": geo.boolean,
    "bevel": mod.bevel,
    "solidify": mod.solidify,
    "subdivide": mod.subdivide,
    "mirror": mod.mirror,
    "array": mod.array,
    "remesh": mod.remesh,
    "decimate": mod.decimate,
    # transforms
    "move_object": xf.move_object,
    "rotate_object": xf.rotate_object,
    "scale_object": xf.scale_object,
    "set_dimensions": xf.set_dimensions,
    "apply_transform": geo.apply_transform,
    "set_origin": geo.set_origin,
    # objects
    "duplicate_object": obj.duplicate_object,
    "delete_object": obj.delete_object,
    "rename_object": obj.rename_object,
    "parent_object": obj.parent_object,
    "unparent_object": obj.unparent_object,
    "set_visibility": obj.set_visibility,
    "join_objects": geo.join_objects,
    "separate_objects": geo.separate_objects,
    "create_collection": col.create_collection,
    "move_to_collection": col.move_to_collection,
    # materials
    "create_material": mat.create_material,
    "update_material": mat.update_material,
    "assign_material": mat.assign_material,
    "set_base_color": mat.set_base_color,
    "set_metallic": mat.set_metallic,
    "set_roughness": mat.set_roughness,
    "set_transparency": mat.set_transparency,
    "assign_texture": tex.assign_texture,
    "remove_texture": tex.remove_texture,
    # cameras, lights, world
    "add_camera": cam.add_camera,
    "update_camera": cam.update_camera,
    "add_light": cam.add_light,
    "update_light": cam.update_light,
    "set_world_background": cam.set_world_background,
}
