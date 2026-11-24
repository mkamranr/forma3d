"""Modeling DSL v1.0 — operation schemas (spec §8.2–8.3).

Every operation is a strict Pydantic model selected by `operation_type`. Units are meters and
degrees; Blender's Z axis is up. Adding an operation requires: a model here, registration in
`OPERATION_MODELS`, validator rules, a Blender handler in `bl_runtime`, and tests.
"""

from __future__ import annotations

from typing import Annotated, Literal, Optional, Union

from pydantic import Field, TypeAdapter, model_validator

from forma3d_shared import DSL_SCHEMA_VERSION
from forma3d_shared.schemas.common import (
    Angle3,
    CollectionId,
    Dim2,
    Dim3,
    Dimension,
    HexColor,
    MaterialId,
    MetadataValue,
    ObjectId,
    OperationId,
    SafeName,
    SafeText,
    Scale3,
    ScaleFactor,
    StrictModel,
    Token,
    UnitInterval,
    Vec3,
)

# --------------------------------------------------------------------------- parameters


class PlacementParams(StrictModel):
    """Common to every create_* primitive."""

    name: SafeName
    location: Vec3 = (0.0, 0.0, 0.0)
    rotation: Angle3 = (0.0, 0.0, 0.0)
    parent_id: Optional[ObjectId] = None
    collection_id: Optional[CollectionId] = None
    material_id: Optional[MaterialId] = None


class CubeParams(PlacementParams):
    dimensions: Dim3 = (1.0, 1.0, 1.0)


class SphereParams(PlacementParams):
    radius: Dimension = 0.5
    segments: int = Field(default=32, ge=3, le=128)
    rings: int = Field(default=16, ge=2, le=64)


class CylinderParams(PlacementParams):
    radius: Dimension = 0.5
    depth: Dimension = 1.0
    vertices: int = Field(default=32, ge=3, le=128)


class ConeParams(PlacementParams):
    radius_bottom: Dimension = 0.5
    radius_top: float = Field(default=0.0, ge=0.0, le=100.0)
    depth: Dimension = 1.0
    vertices: int = Field(default=32, ge=3, le=128)


class TorusParams(PlacementParams):
    major_radius: Dimension = 0.5
    minor_radius: Dimension = 0.1
    major_segments: int = Field(default=48, ge=3, le=128)
    minor_segments: int = Field(default=12, ge=3, le=64)

    @model_validator(mode="after")
    def _ring_not_self_intersecting(self) -> TorusParams:
        if self.minor_radius >= self.major_radius:
            raise ValueError("minor_radius must be smaller than major_radius")
        return self


class PlaneParams(PlacementParams):
    size: Dim2 = (1.0, 1.0)


class MoveParams(StrictModel):
    location: Optional[Vec3] = None
    offset: Optional[Vec3] = None

    @model_validator(mode="after")
    def _exactly_one(self) -> MoveParams:
        if (self.location is None) == (self.offset is None):
            raise ValueError("provide exactly one of 'location' (absolute) or 'offset' (relative)")
        return self


class RotateParams(StrictModel):
    rotation: Optional[Angle3] = None
    delta: Optional[Angle3] = None

    @model_validator(mode="after")
    def _exactly_one(self) -> RotateParams:
        if (self.rotation is None) == (self.delta is None):
            raise ValueError("provide exactly one of 'rotation' (absolute degrees) or 'delta' (relative degrees)")
        return self


class ScaleParams(StrictModel):
    scale: Optional[Scale3] = None
    factor: Optional[ScaleFactor] = None

    @model_validator(mode="after")
    def _exactly_one(self) -> ScaleParams:
        if (self.scale is None) == (self.factor is None):
            raise ValueError("provide exactly one of 'scale' (absolute per-axis) or 'factor' (uniform multiplier)")
        return self


class DimensionsParams(StrictModel):
    dimensions: Dim3


class DuplicateParams(StrictModel):
    new_object_id: ObjectId
    name: SafeName
    offset: Vec3 = (0.0, 0.0, 0.0)


class DeleteParams(StrictModel):
    recursive: bool = False


class RenameParams(StrictModel):
    name: SafeName


class ParentParams(StrictModel):
    parent_id: ObjectId
    keep_transform: bool = True


class UnparentParams(StrictModel):
    keep_transform: bool = True


class VisibilityParams(StrictModel):
    visible: bool


class CreateCollectionParams(StrictModel):
    collection_id: CollectionId
    name: SafeName
    parent_collection_id: Optional[CollectionId] = None


class MoveToCollectionParams(StrictModel):
    collection_id: CollectionId


class CreateMaterialParams(StrictModel):
    material_id: MaterialId
    name: SafeName
    base_color: HexColor = "#CCCCCC"
    metallic: UnitInterval = 0.0
    roughness: UnitInterval = 0.5
    alpha: UnitInterval = 1.0


class UpdateMaterialParams(StrictModel):
    material_id: MaterialId
    name: Optional[SafeName] = None
    base_color: Optional[HexColor] = None
    metallic: Optional[UnitInterval] = None
    roughness: Optional[UnitInterval] = None
    alpha: Optional[UnitInterval] = None

    @model_validator(mode="after")
    def _something_to_update(self) -> UpdateMaterialParams:
        if all(getattr(self, f) is None for f in ("name", "base_color", "metallic", "roughness", "alpha")):
            raise ValueError("update_material needs at least one property to change")
        return self


class AssignMaterialParams(StrictModel):
    material_id: MaterialId


class SetBaseColorParams(StrictModel):
    material_id: MaterialId
    base_color: HexColor


class SetMetallicParams(StrictModel):
    material_id: MaterialId
    metallic: UnitInterval


class SetRoughnessParams(StrictModel):
    material_id: MaterialId
    roughness: UnitInterval


class SetTransparencyParams(StrictModel):
    material_id: MaterialId
    alpha: UnitInterval


class BevelParams(StrictModel):
    width: float = Field(default=0.01, ge=0.0001, le=1.0)
    segments: int = Field(default=3, ge=1, le=64)
    limit_method: Literal["angle", "none"] = "angle"
    angle: float = Field(default=30.0, ge=0.0, le=180.0)


class SolidifyParams(StrictModel):
    thickness: float = Field(default=0.01, ge=-1.0, le=1.0)
    offset: float = Field(default=-1.0, ge=-1.0, le=1.0)

    @model_validator(mode="after")
    def _non_zero(self) -> SolidifyParams:
        if self.thickness == 0:
            raise ValueError("thickness must not be zero")
        return self


# --------------------------------------------------------------------------- operations


class _Op(StrictModel):
    operation_id: OperationId
    dependencies: list[OperationId] = Field(default_factory=list, max_length=50)
    metadata: dict[Token, MetadataValue] = Field(default_factory=dict, max_length=20)


class _TargetOp(_Op):
    target_object_id: ObjectId


class _NoTargetOp(_Op):
    target_object_id: None = None


# Geometry
class CreateCube(_TargetOp):
    operation_type: Literal["create_cube"]
    parameters: CubeParams


class CreateSphere(_TargetOp):
    operation_type: Literal["create_sphere"]
    parameters: SphereParams


class CreateCylinder(_TargetOp):
    operation_type: Literal["create_cylinder"]
    parameters: CylinderParams


class CreateCone(_TargetOp):
    operation_type: Literal["create_cone"]
    parameters: ConeParams


class CreateTorus(_TargetOp):
    operation_type: Literal["create_torus"]
    parameters: TorusParams


class CreatePlane(_TargetOp):
    operation_type: Literal["create_plane"]
    parameters: PlaneParams


class Bevel(_TargetOp):
    operation_type: Literal["bevel"]
    parameters: BevelParams = BevelParams()


class Solidify(_TargetOp):
    operation_type: Literal["solidify"]
    parameters: SolidifyParams = SolidifyParams()


# Transforms
class MoveObject(_TargetOp):
    operation_type: Literal["move_object"]
    parameters: MoveParams


class RotateObject(_TargetOp):
    operation_type: Literal["rotate_object"]
    parameters: RotateParams


class ScaleObject(_TargetOp):
    operation_type: Literal["scale_object"]
    parameters: ScaleParams


class SetDimensions(_TargetOp):
    operation_type: Literal["set_dimensions"]
    parameters: DimensionsParams


# Objects
class DuplicateObject(_TargetOp):
    operation_type: Literal["duplicate_object"]
    parameters: DuplicateParams


class DeleteObject(_TargetOp):
    operation_type: Literal["delete_object"]
    parameters: DeleteParams = DeleteParams()


class RenameObject(_TargetOp):
    operation_type: Literal["rename_object"]
    parameters: RenameParams


class ParentObject(_TargetOp):
    operation_type: Literal["parent_object"]
    parameters: ParentParams


class UnparentObject(_TargetOp):
    operation_type: Literal["unparent_object"]
    parameters: UnparentParams = UnparentParams()


class SetVisibility(_TargetOp):
    operation_type: Literal["set_visibility"]
    parameters: VisibilityParams


class CreateCollection(_NoTargetOp):
    operation_type: Literal["create_collection"]
    parameters: CreateCollectionParams


class MoveToCollection(_TargetOp):
    operation_type: Literal["move_to_collection"]
    parameters: MoveToCollectionParams


# Materials
class CreateMaterial(_NoTargetOp):
    operation_type: Literal["create_material"]
    parameters: CreateMaterialParams


class UpdateMaterial(_NoTargetOp):
    operation_type: Literal["update_material"]
    parameters: UpdateMaterialParams


class AssignMaterial(_TargetOp):
    operation_type: Literal["assign_material"]
    parameters: AssignMaterialParams


class SetBaseColor(_NoTargetOp):
    operation_type: Literal["set_base_color"]
    parameters: SetBaseColorParams


class SetMetallic(_NoTargetOp):
    operation_type: Literal["set_metallic"]
    parameters: SetMetallicParams


class SetRoughness(_NoTargetOp):
    operation_type: Literal["set_roughness"]
    parameters: SetRoughnessParams


class SetTransparency(_NoTargetOp):
    operation_type: Literal["set_transparency"]
    parameters: SetTransparencyParams


OPERATION_MODELS: tuple[type[_Op], ...] = (
    CreateCube,
    CreateSphere,
    CreateCylinder,
    CreateCone,
    CreateTorus,
    CreatePlane,
    Bevel,
    Solidify,
    MoveObject,
    RotateObject,
    ScaleObject,
    SetDimensions,
    DuplicateObject,
    DeleteObject,
    RenameObject,
    ParentObject,
    UnparentObject,
    SetVisibility,
    CreateCollection,
    MoveToCollection,
    CreateMaterial,
    UpdateMaterial,
    AssignMaterial,
    SetBaseColor,
    SetMetallic,
    SetRoughness,
    SetTransparency,
)

#: Explicit allowlist (spec §8.2). Blender's runtime registry must match this exactly.
ALLOWED_OPERATIONS: frozenset[str] = frozenset(
    m.model_fields["operation_type"].annotation.__args__[0]
    for m in OPERATION_MODELS  # type: ignore[union-attr]
)

CREATE_PRIMITIVE_TYPES = frozenset(
    {"create_cube", "create_sphere", "create_cylinder", "create_cone", "create_torus", "create_plane"}
)

Operation = Annotated[
    Union[
        CreateCube,
        CreateSphere,
        CreateCylinder,
        CreateCone,
        CreateTorus,
        CreatePlane,
        Bevel,
        Solidify,
        MoveObject,
        RotateObject,
        ScaleObject,
        SetDimensions,
        DuplicateObject,
        DeleteObject,
        RenameObject,
        ParentObject,
        UnparentObject,
        SetVisibility,
        CreateCollection,
        MoveToCollection,
        CreateMaterial,
        UpdateMaterial,
        AssignMaterial,
        SetBaseColor,
        SetMetallic,
        SetRoughness,
        SetTransparency,
    ],
    Field(discriminator="operation_type"),
]

operation_adapter: TypeAdapter[Operation] = TypeAdapter(Operation)


class OperationBatch(StrictModel):
    schema_version: Literal["1.0"] = DSL_SCHEMA_VERSION
    summary: SafeText = ""
    # Hard structural cap; configurable limits are enforced by the validator.
    operations: list[Operation] = Field(min_length=1, max_length=2000)
