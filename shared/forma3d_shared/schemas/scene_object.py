"""Scene inventory contract — produced by the Blender scene inspector, stored with every version,
and used by the validator / agents as the authoritative view of the current scene."""

from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, ConfigDict, Field

from forma3d_shared import INVENTORY_SCHEMA_VERSION


class _Snapshot(BaseModel):
    # Produced by trusted code; tolerate additive fields from newer runtimes.
    model_config = ConfigDict(extra="ignore")


class ModifierSnapshot(_Snapshot):
    name: str
    type: str
    params: dict[str, float | int | str | bool] = Field(default_factory=dict)


class MeshStats(_Snapshot):
    vertices: int = 0
    edges: int = 0
    faces: int = 0
    triangles: int = 0


class CameraSnapshot(_Snapshot):
    lens: float = 50.0
    active: bool = False


class LightSnapshot(_Snapshot):
    light_type: str = "point"
    color: str = "#FFFFFF"
    energy: float = 0.0
    radius: float = 0.0
    spot_angle: Optional[float] = None


class TextureSnapshot(_Snapshot):
    pattern: Optional[str] = None
    image_id: Optional[str] = None
    color_a: Optional[str] = None
    color_b: Optional[str] = None
    scale: float = 1.0
    rotation: float = 0.0


class WorldSnapshot(_Snapshot):
    color: str = "#202630"
    strength: float = 1.0


class ObjectSnapshot(_Snapshot):
    object_id: str
    name: str
    type: str = "MESH"
    component_type: Optional[str] = None
    parent_id: Optional[str] = None
    collection_id: Optional[str] = None
    location: tuple[float, float, float] = (0.0, 0.0, 0.0)
    rotation: tuple[float, float, float] = (0.0, 0.0, 0.0)  # degrees, XYZ euler
    scale: tuple[float, float, float] = (1.0, 1.0, 1.0)
    dimensions: tuple[float, float, float] = (0.0, 0.0, 0.0)
    visible: bool = True
    material_ids: list[str] = Field(default_factory=list)
    modifiers: list[ModifierSnapshot] = Field(default_factory=list)
    mesh: MeshStats = Field(default_factory=MeshStats)
    camera: Optional[CameraSnapshot] = None
    light: Optional[LightSnapshot] = None
    created_version: Optional[int] = None
    last_modified_version: Optional[int] = None


class MaterialSnapshot(_Snapshot):
    material_id: str
    name: str
    base_color: str = "#CCCCCC"
    metallic: float = 0.0
    roughness: float = 0.5
    alpha: float = 1.0
    emission_color: Optional[str] = None
    emission_strength: float = 0.0
    transmission: float = 0.0
    ior: float = 1.45
    texture: Optional[TextureSnapshot] = None


class CollectionSnapshot(_Snapshot):
    collection_id: str
    name: str
    parent_id: Optional[str] = None


class SceneInventory(_Snapshot):
    schema_version: str = INVENTORY_SCHEMA_VERSION
    units: str = "METERS"
    objects: list[ObjectSnapshot] = Field(default_factory=list)
    materials: list[MaterialSnapshot] = Field(default_factory=list)
    collections: list[CollectionSnapshot] = Field(default_factory=list)
    world: Optional[WorldSnapshot] = None

    def object_map(self) -> dict[str, ObjectSnapshot]:
        return {o.object_id: o for o in self.objects}

    @property
    def total_vertices(self) -> int:
        return sum(o.mesh.vertices for o in self.objects)
