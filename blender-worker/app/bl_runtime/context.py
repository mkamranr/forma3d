"""Runtime execution context: stable-id indexes over the Blender scene."""

from __future__ import annotations

import bpy

PROP_OBJECT_ID = "app_object_id"
PROP_PROJECT_ID = "app_project_id"
PROP_COMPONENT = "app_component_type"
PROP_CREATED = "app_created_version"
PROP_MODIFIED = "app_last_modified_version"
PROP_PARENT = "app_parent_id"
PROP_VISIBLE = "app_visible"
PROP_MATERIAL_ID = "app_material_id"
PROP_BASE_COLOR = "app_base_color"
PROP_COLLECTION_ID = "app_collection_id"


class OpError(Exception):
    """Deterministic, user-explainable failure of a single operation."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


class RuntimeContext:
    def __init__(self, project_id: str, version_number: int) -> None:
        self.project_id = project_id
        self.version = version_number
        self.objects: dict[str, bpy.types.Object] = {}
        self.materials: dict[str, bpy.types.Material] = {}
        self.collections: dict[str, bpy.types.Collection] = {}
        self.touched: set[str] = set()
        self.reindex()

    def reindex(self) -> None:
        self.objects = {o[PROP_OBJECT_ID]: o for o in bpy.data.objects if PROP_OBJECT_ID in o}
        self.materials = {m[PROP_MATERIAL_ID]: m for m in bpy.data.materials if PROP_MATERIAL_ID in m}
        self.collections = {c[PROP_COLLECTION_ID]: c for c in bpy.data.collections if PROP_COLLECTION_ID in c}

    @property
    def scene(self) -> bpy.types.Scene:
        return bpy.context.scene

    def obj(self, object_id: str) -> bpy.types.Object:
        try:
            return self.objects[object_id]
        except KeyError:
            raise OpError("unknown_object", f"object '{object_id}' does not exist") from None

    def material(self, material_id: str) -> bpy.types.Material:
        try:
            return self.materials[material_id]
        except KeyError:
            raise OpError("unknown_material", f"material '{material_id}' does not exist") from None

    def collection(self, collection_id: str) -> bpy.types.Collection:
        try:
            return self.collections[collection_id]
        except KeyError:
            raise OpError("unknown_collection", f"collection '{collection_id}' does not exist") from None

    def mark_modified(self, obj: bpy.types.Object) -> None:
        obj[PROP_MODIFIED] = self.version
        self.touched.add(obj[PROP_OBJECT_ID])

    def update(self) -> None:
        """Refresh matrix_world etc. after transform changes."""
        bpy.context.view_layer.update()
