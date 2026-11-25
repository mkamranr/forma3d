"""Backend <-> Blender worker job contract. All paths are relative to DATA_ROOT and generated
server-side from ids; the worker re-checks them before touching the filesystem."""

from __future__ import annotations

import uuid
from typing import Any, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, model_validator

from forma3d_shared.schemas.scene_object import SceneInventory

RelPath = str
Sha256 = str

BLENDER_JOB_STREAM = "jobs:blender"
BLENDER_JOB_GROUP = "blender-workers"

ArtifactKind = Literal["blend", "glb", "thumbnail", "inventory", "operations", "render", "export"]
JobKind = Literal["modeling", "render", "export"]
ExportFormat = Literal["gltf", "obj", "stl"]
#: Formats a user can download. GLB is the version's own artifact; the others are export jobs.
DOWNLOAD_FORMATS: tuple[str, ...] = ("glb", "gltf", "obj", "stl")

#: Filenames inside a version directory (spec §12).
ARTIFACT_FILENAMES: dict[str, str] = {
    "blend": "scene.blend",
    "glb": "scene.glb",
    "thumbnail": "thumbnail.png",
    "inventory": "inventory.json",
    "operations": "operations.json",
}
REQUIRED_ARTIFACTS: frozenset[str] = frozenset({"blend", "glb", "inventory", "operations"})
RENDER_FILENAME = "render.png"
#: Export outputs. Multi-file formats (glTF + .bin + textures, OBJ + .mtl + textures) are zipped.
EXPORT_FILENAMES: dict[str, str] = {"gltf": "scene-gltf.zip", "obj": "scene-obj.zip", "stl": "scene.stl"}
UNIT_SCALE: dict[str, float] = {"m": 1.0, "cm": 100.0, "mm": 1000.0}

#: Cycles samples per quality profile (spec §9.6); Eevee uses its own TAA samples.
RENDER_PROFILES: dict[str, dict[str, int]] = {
    "draft": {"cycles_samples": 16, "eevee_samples": 16},
    "standard": {"cycles_samples": 64, "eevee_samples": 32},
    "high": {"cycles_samples": 256, "eevee_samples": 64},
}


class RenderSettings(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    engine: Literal["cycles", "eevee"] = "cycles"
    quality: Literal["draft", "standard", "high"] = "standard"
    width: int = Field(default=1280, ge=64, le=8192)
    height: int = Field(default=720, ge=64, le=8192)
    camera_object_id: Optional[str] = Field(default=None, pattern=r"^[a-z][a-z0-9_]{0,63}$")
    transparent_background: bool = False


class ExportSettings(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    format: ExportFormat
    unit: Literal["m", "cm", "mm"] = "m"
    include_hidden: bool = False


class TextureFile(BaseModel):
    """An uploaded texture image a modeling batch references (verified by checksum)."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    image_id: uuid.UUID
    path: RelPath
    sha256: Sha256


class SourceVersion(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    version_id: uuid.UUID
    version_number: int
    blend_path: RelPath
    blend_sha256: Sha256


class BlenderJobSpec(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    job_id: uuid.UUID
    kind: JobKind = "modeling"
    project_id: uuid.UUID
    user_id: uuid.UUID
    #: Modeling jobs only (render/export jobs read an existing version and change nothing).
    operation_batch_id: Optional[uuid.UUID] = None
    #: Version number the result will be committed as (app_created/last_modified_version).
    target_version_number: int = Field(ge=1)
    source: Optional[SourceVersion] = None
    staging_dir: RelPath
    #: Canonical, already-validated operations; the worker re-validates them.
    operations: list[dict[str, Any]]
    batch_sha256: Sha256
    render_thumbnail: bool = True
    thumbnail_size: int = Field(default=512, ge=64, le=4096)
    timeout_seconds: int = Field(default=300, ge=10, le=3600)
    textures: list[TextureFile] = Field(default_factory=list, max_length=50)
    render: Optional[RenderSettings] = None
    export: Optional[ExportSettings] = None

    @model_validator(mode="after")
    def _kind(self) -> BlenderJobSpec:
        if self.kind == "modeling":
            if self.operation_batch_id is None or not self.operations:
                raise ValueError("modeling jobs need an operation batch")
            if self.render or self.export:
                raise ValueError("modeling jobs take no render/export settings")
        else:
            if self.source is None:
                raise ValueError(f"{self.kind} jobs need a source version")
            if self.operations or self.textures:
                raise ValueError(f"{self.kind} jobs must not carry operations")
            if (self.kind == "render") != (self.render is not None) or (self.kind == "export") != (
                self.export is not None
            ):
                raise ValueError(f"{self.kind} jobs need matching settings")
        return self


class Artifact(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: ArtifactKind
    filename: str
    size: int = Field(ge=0)
    sha256: Sha256


class BlenderJobResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    job_id: uuid.UUID
    success: bool
    worker_id: str = ""
    artifacts: list[Artifact] = Field(default_factory=list)
    inventory: Optional[SceneInventory] = None
    failed_operation_id: Optional[str] = None
    error_code: Optional[str] = None
    error_message: Optional[str] = None
    duration_ms: int = 0
    blender_version: Optional[str] = None
    log_tail: str = ""
    #: render jobs: engine, samples, camera and lights actually used
    details: dict[str, Any] = Field(default_factory=dict)


class JobStartResponse(BaseModel):
    """Backend reply to the worker's start claim. `proceed=False` means drop the job (dedupe/cancel)."""

    proceed: bool
    reason: str = ""
