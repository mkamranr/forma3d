"""Request/response models for the scene + direct-edit API. Values reuse the DSL's bounded types so
out-of-range input is rejected at the edge with a 422 before any batch is built."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict

from app.schemas.modeling import JobOut, VersionOut
from forma3d_shared.schemas.common import Angle3, Dim3, HexColor, MaterialId, SafeName, UnitInterval, Vec3


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


class NewObject(_In):
    kind: Literal["cube", "sphere", "cylinder", "cone", "plane", "torus"]
    name: SafeName | None = None
    location: Vec3 = (0.0, 0.0, 0.5)


class MaterialPatch(_In):
    name: SafeName | None = None
    base_color: HexColor | None = None
    metallic: UnitInterval | None = None
    roughness: UnitInterval | None = None
    alpha: UnitInterval | None = None


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
