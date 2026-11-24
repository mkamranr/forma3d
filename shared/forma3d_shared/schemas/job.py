"""Backend <-> Blender worker job contract. All paths are relative to DATA_ROOT and generated
server-side from ids; the worker re-checks them before touching the filesystem."""

from __future__ import annotations

import uuid
from typing import Any, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field

from forma3d_shared.schemas.scene_object import SceneInventory

RelPath = str
Sha256 = str

BLENDER_JOB_STREAM = "jobs:blender"
BLENDER_JOB_GROUP = "blender-workers"

ArtifactKind = Literal["blend", "glb", "thumbnail", "inventory", "operations"]

#: Filenames inside a version directory (spec §12).
ARTIFACT_FILENAMES: dict[str, str] = {
    "blend": "scene.blend",
    "glb": "scene.glb",
    "thumbnail": "thumbnail.png",
    "inventory": "inventory.json",
    "operations": "operations.json",
}
REQUIRED_ARTIFACTS: frozenset[str] = frozenset({"blend", "glb", "inventory", "operations"})


class SourceVersion(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    version_id: uuid.UUID
    version_number: int
    blend_path: RelPath
    blend_sha256: Sha256


class BlenderJobSpec(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    job_id: uuid.UUID
    project_id: uuid.UUID
    user_id: uuid.UUID
    operation_batch_id: uuid.UUID
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


class JobStartResponse(BaseModel):
    """Backend reply to the worker's start claim. `proceed=False` means drop the job (dedupe/cancel)."""

    proceed: bool
    reason: str = ""
