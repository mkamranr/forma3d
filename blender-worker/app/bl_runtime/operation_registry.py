"""Explicit allowlist of DSL operation handlers. Must equal forma3d_shared ALLOWED_OPERATIONS
(enforced by tests/test_registry_parity.py)."""

from __future__ import annotations

from collections.abc import Callable

from . import collection_manager as col
from . import material_manager as mat
from . import modifier_manager as mod
from . import object_factory as obj
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
    "bevel": mod.bevel,
    "solidify": mod.solidify,
    # transforms
    "move_object": xf.move_object,
    "rotate_object": xf.rotate_object,
    "scale_object": xf.scale_object,
    "set_dimensions": xf.set_dimensions,
    # objects
    "duplicate_object": obj.duplicate_object,
    "delete_object": obj.delete_object,
    "rename_object": obj.rename_object,
    "parent_object": obj.parent_object,
    "unparent_object": obj.unparent_object,
    "set_visibility": obj.set_visibility,
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
}
