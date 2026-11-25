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
    Angle,
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

Axis = Literal["x", "y", "z"]
#: Faces selected by their (object-space) normal. Front is -Y, right is +X, top is +Z.
Side = Literal["top", "bottom", "front", "back", "left", "right"]
TextureKind = Literal["wood", "marble", "noise", "checker", "brick", "fabric", "concrete", "brushed_metal"]

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


class CircleParams(PlacementParams):
    """A flat filled disc in the XY plane (extrude it to make a cylinder-like solid)."""

    radius: Dimension = 0.5
    vertices: int = Field(default=32, ge=3, le=128)
    fill: Literal["ngon", "triangle_fan"] = "ngon"


Face = Annotated[list[Annotated[int, Field(ge=0)]], Field(min_length=3, max_length=64)]


class MeshParams(PlacementParams):
    """Custom geometry: object-space vertices (meters) and faces as vertex-index loops."""

    vertices: list[Vec3] = Field(min_length=3, max_length=5000)
    faces: list[Face] = Field(min_length=1, max_length=10000)

    @model_validator(mode="after")
    def _indices(self) -> MeshParams:
        n = len(self.vertices)
        for i, face in enumerate(self.faces):
            if any(v >= n for v in face):
                raise ValueError(f"face {i} references a vertex index >= {n}")
            if len(set(face)) != len(face):
                raise ValueError(f"face {i} repeats a vertex")
        return self


class CurveParams(PlacementParams):
    """A path swept with a round profile into a tube mesh (rails, handles, pipes, wire frames)."""

    points: list[Vec3] = Field(min_length=2, max_length=200)  # object space, meters
    curve_type: Literal["poly", "smooth"] = "smooth"
    cyclic: bool = False
    thickness: Dimension = 0.02  # tube diameter
    profile_resolution: int = Field(default=4, ge=0, le=8)
    resolution: int = Field(default=12, ge=1, le=32)  # samples per segment for smooth curves


class ExtrudeParams(StrictModel):
    side: Side = "top"
    distance: float = Field(ge=-10.0, le=10.0)

    @model_validator(mode="after")
    def _non_zero(self) -> ExtrudeParams:
        if abs(self.distance) < 1e-5:
            raise ValueError("distance must not be zero")
        return self


class InsetParams(StrictModel):
    side: Union[Side, Literal["all"]] = "top"
    thickness: float = Field(default=0.02, ge=0.0001, le=1.0)
    depth: float = Field(default=0.0, ge=-1.0, le=1.0)


class BooleanParams(StrictModel):
    operand_id: ObjectId
    keep_operand: bool = False
    solver: Literal["exact", "fast"] = "exact"


class SubdivideParams(StrictModel):
    levels: int = Field(default=2, ge=1, le=6)
    smooth: bool = True


class MirrorParams(StrictModel):
    axes: list[Axis] = Field(default_factory=lambda: ["x"], min_length=1, max_length=3)
    mirror_object_id: Optional[ObjectId] = None
    merge: bool = True
    bisect: bool = False

    @model_validator(mode="after")
    def _unique(self) -> MirrorParams:
        if len(set(self.axes)) != len(self.axes):
            raise ValueError("axes must be unique")
        return self


class ArrayParams(StrictModel):
    count: int = Field(default=2, ge=2, le=1000)
    offset: Vec3 = (1.0, 0.0, 0.0)  # constant step between copies, object space, meters
    merge: bool = False


class RemeshParams(StrictModel):
    voxel_size: float = Field(default=0.02, ge=0.002, le=2.0)
    smooth_shading: bool = True


class DecimateParams(StrictModel):
    ratio: float = Field(default=0.5, ge=0.01, le=1.0)


class ApplyTransformParams(StrictModel):
    location: bool = False
    rotation: bool = True
    scale: bool = True

    @model_validator(mode="after")
    def _something(self) -> ApplyTransformParams:
        if not (self.location or self.rotation or self.scale):
            raise ValueError("apply at least one of location, rotation or scale")
        return self


class SetOriginParams(StrictModel):
    mode: Literal["geometry_center", "bounds_bottom", "world_origin", "point"] = "geometry_center"
    point: Optional[Vec3] = None  # world space, for mode='point'

    @model_validator(mode="after")
    def _point(self) -> SetOriginParams:
        if (self.mode == "point") != (self.point is not None):
            raise ValueError("'point' is required for mode 'point' and only allowed there")
        return self


class JoinParams(StrictModel):
    object_ids: list[ObjectId] = Field(min_length=1, max_length=100)

    @model_validator(mode="after")
    def _unique(self) -> JoinParams:
        if len(set(self.object_ids)) != len(self.object_ids):
            raise ValueError("object_ids must be unique")
        return self


class SeparateParams(StrictModel):
    """Split loose parts (or material regions) into new objects. The part containing the most
    vertices stays as the target; the others get `new_object_ids` in order of their centres
    (X, then Y, then Z). The count must match the number of parts exactly."""

    mode: Literal["loose_parts", "material"] = "loose_parts"
    new_object_ids: list[ObjectId] = Field(min_length=1, max_length=100)
    names: Optional[list[SafeName]] = None

    @model_validator(mode="after")
    def _consistent(self) -> SeparateParams:
        if len(set(self.new_object_ids)) != len(self.new_object_ids):
            raise ValueError("new_object_ids must be unique")
        if self.names is not None and len(self.names) != len(self.new_object_ids):
            raise ValueError("names must match new_object_ids")
        return self


class CameraParams(StrictModel):
    name: SafeName
    location: Vec3
    look_at: Optional[Vec3] = None
    rotation: Optional[Angle3] = None
    lens: float = Field(default=50.0, ge=8.0, le=300.0)  # focal length, mm
    set_active: bool = True
    parent_id: Optional[ObjectId] = None

    @model_validator(mode="after")
    def _aim(self) -> CameraParams:
        if self.look_at is not None and self.rotation is not None:
            raise ValueError("use either look_at or rotation")
        return self


class UpdateCameraParams(StrictModel):
    location: Optional[Vec3] = None
    look_at: Optional[Vec3] = None
    rotation: Optional[Angle3] = None
    lens: Optional[float] = Field(default=None, ge=8.0, le=300.0)
    set_active: Optional[bool] = None

    @model_validator(mode="after")
    def _something(self) -> UpdateCameraParams:
        if self.look_at is not None and self.rotation is not None:
            raise ValueError("use either look_at or rotation")
        if all(getattr(self, f) is None for f in ("location", "look_at", "rotation", "lens", "set_active")):
            raise ValueError("update_camera needs at least one property to change")
        return self


LightType = Literal["point", "sun", "spot", "area"]


class LightParams(StrictModel):
    name: SafeName
    light_type: LightType = "point"
    location: Vec3 = (0.0, 0.0, 3.0)
    look_at: Optional[Vec3] = None
    rotation: Optional[Angle3] = None
    color: HexColor = "#FFFFFF"
    # Watts for point/spot/area; irradiance (W/m², ~1-10) for sun.
    energy: float = Field(default=500.0, ge=0.0, le=100000.0)
    radius: float = Field(default=0.1, ge=0.0, le=20.0)  # soft-shadow size (area: edge length)
    spot_angle: Angle = 45.0
    parent_id: Optional[ObjectId] = None

    @model_validator(mode="after")
    def _aim(self) -> LightParams:
        if self.look_at is not None and self.rotation is not None:
            raise ValueError("use either look_at or rotation")
        if not 1.0 <= self.spot_angle <= 180.0:
            raise ValueError("spot_angle must be between 1 and 180 degrees")
        return self


class UpdateLightParams(StrictModel):
    location: Optional[Vec3] = None
    look_at: Optional[Vec3] = None
    rotation: Optional[Angle3] = None
    color: Optional[HexColor] = None
    energy: Optional[float] = Field(default=None, ge=0.0, le=100000.0)
    radius: Optional[float] = Field(default=None, ge=0.0, le=20.0)
    spot_angle: Optional[float] = Field(default=None, ge=1.0, le=180.0)

    @model_validator(mode="after")
    def _something(self) -> UpdateLightParams:
        if self.look_at is not None and self.rotation is not None:
            raise ValueError("use either look_at or rotation")
        fields = ("location", "look_at", "rotation", "color", "energy", "radius", "spot_angle")
        if all(getattr(self, f) is None for f in fields):
            raise ValueError("update_light needs at least one property to change")
        return self


class WorldBackgroundParams(StrictModel):
    color: HexColor = "#202630"
    strength: float = Field(default=1.0, ge=0.0, le=10.0)


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
    emission_color: Optional[HexColor] = None
    emission_strength: float = Field(default=0.0, ge=0.0, le=100.0)
    transmission: UnitInterval = 0.0  # 1 = glass
    ior: float = Field(default=1.45, ge=1.0, le=3.0)


_MATERIAL_FIELDS = (
    "name",
    "base_color",
    "metallic",
    "roughness",
    "alpha",
    "emission_color",
    "emission_strength",
    "transmission",
    "ior",
)


class UpdateMaterialParams(StrictModel):
    material_id: MaterialId
    name: Optional[SafeName] = None
    base_color: Optional[HexColor] = None
    metallic: Optional[UnitInterval] = None
    roughness: Optional[UnitInterval] = None
    alpha: Optional[UnitInterval] = None
    emission_color: Optional[HexColor] = None
    emission_strength: Optional[float] = Field(default=None, ge=0.0, le=100.0)
    transmission: Optional[UnitInterval] = None
    ior: Optional[float] = Field(default=None, ge=1.0, le=3.0)

    @model_validator(mode="after")
    def _something_to_update(self) -> UpdateMaterialParams:
        if all(getattr(self, f) is None for f in _MATERIAL_FIELDS):
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


ImageId = Annotated[str, Field(pattern=r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")]


class AssignTextureParams(StrictModel):
    """Give a material a colour texture: a procedural pattern (generated as an image so it survives
    GLB/OBJ export) or an uploaded image identified by its server-side asset id. Never a path."""

    material_id: MaterialId
    pattern: Optional[TextureKind] = None
    image_id: Optional[ImageId] = None
    color_a: Optional[HexColor] = None  # pattern colours (defaults: the material colour, darker)
    color_b: Optional[HexColor] = None
    scale: float = Field(default=1.0, ge=0.05, le=50.0)  # pattern/image repeats per meter
    rotation: float = Field(default=0.0, ge=-360.0, le=360.0)  # degrees (e.g. wood grain direction)

    @model_validator(mode="after")
    def _source(self) -> AssignTextureParams:
        if (self.pattern is None) == (self.image_id is None):
            raise ValueError("provide exactly one of 'pattern' or 'image_id'")
        if self.image_id is not None and (self.color_a or self.color_b):
            raise ValueError("color_a/color_b apply to patterns only")
        return self


class RemoveTextureParams(StrictModel):
    material_id: MaterialId


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


class CreateCircle(_TargetOp):
    operation_type: Literal["create_circle"]
    parameters: CircleParams


class CreateMesh(_TargetOp):
    operation_type: Literal["create_mesh"]
    parameters: MeshParams


class CreateCurve(_TargetOp):
    operation_type: Literal["create_curve"]
    parameters: CurveParams


class Extrude(_TargetOp):
    operation_type: Literal["extrude"]
    parameters: ExtrudeParams


class Inset(_TargetOp):
    operation_type: Literal["inset"]
    parameters: InsetParams = InsetParams()


class BooleanUnion(_TargetOp):
    operation_type: Literal["boolean_union"]
    parameters: BooleanParams


class BooleanDifference(_TargetOp):
    operation_type: Literal["boolean_difference"]
    parameters: BooleanParams


class BooleanIntersect(_TargetOp):
    operation_type: Literal["boolean_intersect"]
    parameters: BooleanParams


class Subdivide(_TargetOp):
    operation_type: Literal["subdivide"]
    parameters: SubdivideParams = SubdivideParams()


class Mirror(_TargetOp):
    operation_type: Literal["mirror"]
    parameters: MirrorParams = MirrorParams()


class Array(_TargetOp):
    operation_type: Literal["array"]
    parameters: ArrayParams = ArrayParams()


class Remesh(_TargetOp):
    operation_type: Literal["remesh"]
    parameters: RemeshParams = RemeshParams()


class Decimate(_TargetOp):
    operation_type: Literal["decimate"]
    parameters: DecimateParams = DecimateParams()


# Transforms
class ApplyTransform(_TargetOp):
    operation_type: Literal["apply_transform"]
    parameters: ApplyTransformParams = ApplyTransformParams()


class SetOrigin(_TargetOp):
    operation_type: Literal["set_origin"]
    parameters: SetOriginParams = SetOriginParams()


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


class JoinObjects(_TargetOp):
    operation_type: Literal["join_objects"]
    parameters: JoinParams


class SeparateObjects(_TargetOp):
    operation_type: Literal["separate_objects"]
    parameters: SeparateParams


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


class AssignTexture(_NoTargetOp):
    operation_type: Literal["assign_texture"]
    parameters: AssignTextureParams


class RemoveTexture(_NoTargetOp):
    operation_type: Literal["remove_texture"]
    parameters: RemoveTextureParams


# Cameras, lights, world
class AddCamera(_TargetOp):
    operation_type: Literal["add_camera"]
    parameters: CameraParams


class UpdateCamera(_TargetOp):
    operation_type: Literal["update_camera"]
    parameters: UpdateCameraParams


class AddLight(_TargetOp):
    operation_type: Literal["add_light"]
    parameters: LightParams


class UpdateLight(_TargetOp):
    operation_type: Literal["update_light"]
    parameters: UpdateLightParams


class SetWorldBackground(_NoTargetOp):
    operation_type: Literal["set_world_background"]
    parameters: WorldBackgroundParams


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
    CreateCircle,
    CreateMesh,
    CreateCurve,
    Extrude,
    Inset,
    BooleanUnion,
    BooleanDifference,
    BooleanIntersect,
    Subdivide,
    Mirror,
    Array,
    Remesh,
    Decimate,
    ApplyTransform,
    SetOrigin,
    JoinObjects,
    SeparateObjects,
    AssignTexture,
    RemoveTexture,
    AddCamera,
    UpdateCamera,
    AddLight,
    UpdateLight,
    SetWorldBackground,
)

#: Explicit allowlist (spec §8.2). Blender's runtime registry must match this exactly.
ALLOWED_OPERATIONS: frozenset[str] = frozenset(
    m.model_fields["operation_type"].annotation.__args__[0]
    for m in OPERATION_MODELS  # type: ignore[union-attr]
)

CREATE_PRIMITIVE_TYPES = frozenset(
    {
        "create_cube",
        "create_sphere",
        "create_cylinder",
        "create_cone",
        "create_torus",
        "create_plane",
        "create_circle",
        "create_mesh",
        "create_curve",
    }
)
BOOLEAN_TYPES = frozenset({"boolean_union", "boolean_difference", "boolean_intersect"})
MODIFIER_TYPES = frozenset({"bevel", "solidify", "subdivide", "mirror", "array", "remesh", "decimate"})
#: Operations that need geometry (a MESH object) as their target.
MESH_ONLY_TYPES = frozenset(
    {
        *MODIFIER_TYPES,
        *BOOLEAN_TYPES,
        "extrude",
        "inset",
        "set_dimensions",
        "assign_material",
        "join_objects",
        "separate_objects",
        "set_origin",
        "apply_transform",
    }
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
        CreateCircle,
        CreateMesh,
        CreateCurve,
        Extrude,
        Inset,
        BooleanUnion,
        BooleanDifference,
        BooleanIntersect,
        Subdivide,
        Mirror,
        Array,
        Remesh,
        Decimate,
        ApplyTransform,
        SetOrigin,
        JoinObjects,
        SeparateObjects,
        AssignTexture,
        RemoveTexture,
        AddCamera,
        UpdateCamera,
        AddLight,
        UpdateLight,
        SetWorldBackground,
    ],
    Field(discriminator="operation_type"),
]

operation_adapter: TypeAdapter[Operation] = TypeAdapter(Operation)


class OperationBatch(StrictModel):
    schema_version: Literal["1.0"] = DSL_SCHEMA_VERSION
    summary: SafeText = ""
    # Hard structural cap; configurable limits are enforced by the validator.
    operations: list[Operation] = Field(min_length=1, max_length=2000)
