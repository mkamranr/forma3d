"""Request/response models for the scene + direct-edit API. Values reuse the DSL's bounded types so
out-of-range input is rejected at the edge with a 422 before any batch is built."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.modeling import JobOut, VersionOut
from forma3d_shared.schemas.common import Angle3, Dim3, HexColor, MaterialId, SafeName, UnitInterval, Vec3
from forma3d_shared.schemas.modeling_operation import TextureKind


class _In(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ObjectPatch(_In):
    """All coordinates are Blender world space (meters, Z up); rotation is local XYZ Euler degrees."""

    name: SafeName | None = None
    location: Vec3 | None = None
    rotation: Angle3 | None = None
    dimensions: Dim3 | None = None
    visible: bool | None = None
    material_id: MaterialId | None = None
    # lights
    light_energy: float | None = Field(default=None, ge=0, le=100000)
    light_color: HexColor | None = None
    # cameras
    camera_lens: float | None = Field(default=None, ge=8, le=300)
    active_camera: bool | None = None


class NewObject(_In):
    kind: Literal["cube", "sphere", "cylinder", "cone", "plane", "torus", "light", "camera"]
    name: SafeName | None = None
    location: Vec3 = (0.0, 0.0, 0.5)


class MaterialPatch(_In):
    name: SafeName | None = None
    base_color: HexColor | None = None
    metallic: UnitInterval | None = None
    roughness: UnitInterval | None = None
    alpha: UnitInterval | None = None
    emission_strength: float | None = Field(default=None, ge=0, le=100)
    transmission: UnitInterval | None = None
    #: procedural pattern, or "" to remove the texture
    texture: TextureKind | Literal[""] | None = None
    #: an uploaded texture image (POST /projects/{id}/textures)
    texture_image_id: str | None = Field(
        default=None, pattern=r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$"
    )
    texture_scale: float | None = Field(default=None, ge=0.05, le=50)


class ObjectMaterial(_In):
    name: SafeName | None = None
    base_color: HexColor | None = None
    metallic: UnitInterval | None = None
    roughness: UnitInterval | None = None
    alpha: UnitInterval | None = None


class SceneOut(BaseModel):
    project_id: str
    version: VersionOut | None
    inventory: dict[str, Any]
    active_job: JobOut | None
