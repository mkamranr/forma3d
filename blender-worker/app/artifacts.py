"""Output artifact validation: presence, size limits, file signatures and cross-checks."""

from __future__ import annotations

import hashlib
import json
import struct
from pathlib import Path

from forma3d_shared.schemas.job import ARTIFACT_FILENAMES, REQUIRED_ARTIFACTS, Artifact
from forma3d_shared.schemas.scene_object import SceneInventory

_BLEND_MAGIC = (b"BLENDER", b"\x28\xb5\x2f\xfd", b"\x1f\x8b")  # raw, zstd, legacy gzip
_PNG_MAGIC = b"\x89PNG\r\n\x1a\n"


class ArtifactError(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def glb_node_object_ids(path: Path) -> set[str]:
    data = path.read_bytes()
    if len(data) < 20 or data[:4] != b"glTF":
        raise ArtifactError("invalid_glb", "GLB file has an invalid header")
    version, length = struct.unpack("<II", data[4:12])
    chunk_len, chunk_type = struct.unpack("<II", data[12:20])
    if version != 2 or length != len(data) or chunk_type != 0x4E4F534A:  # 'JSON'
        raise ArtifactError("invalid_glb", "GLB file is truncated or not glTF 2.0")
    doc = json.loads(data[20 : 20 + chunk_len])
    return {
        n["extras"]["app_object_id"]
        for n in doc.get("nodes", [])
        if isinstance(n.get("extras"), dict) and "app_object_id" in n["extras"]
    }


def validate_outputs(out_dir: Path, *, expect_thumbnail: bool, max_bytes: int) -> tuple[list[Artifact], SceneInventory]:
    required = set(REQUIRED_ARTIFACTS) | ({"thumbnail"} if expect_thumbnail else set())
    artifacts: list[Artifact] = []
    for kind, filename in ARTIFACT_FILENAMES.items():
        path = out_dir / filename
        if not path.is_file():
            if kind in required:
                raise ArtifactError("missing_artifact", f"Blender did not produce {filename}")
            continue
        size = path.stat().st_size
        if size == 0 or size > max_bytes:
            raise ArtifactError("artifact_size", f"{filename} has invalid size {size}")
        head = path.read_bytes()[:8] if kind in ("blend", "thumbnail") else b""
        if kind == "blend" and not head.startswith(_BLEND_MAGIC):
            raise ArtifactError("invalid_blend", "scene.blend is not a Blender file")
        if kind == "thumbnail" and head != _PNG_MAGIC:
            raise ArtifactError("invalid_thumbnail", "thumbnail.png is not a PNG")
        artifacts.append(Artifact(kind=kind, filename=filename, size=size, sha256=sha256_file(path)))

    inventory = SceneInventory.model_validate_json((out_dir / ARTIFACT_FILENAMES["inventory"]).read_bytes())
    exported = glb_node_object_ids(out_dir / ARTIFACT_FILENAMES["glb"])
    missing = {o.object_id for o in inventory.objects} - exported
    if missing:
        raise ArtifactError("glb_incomplete", f"GLB is missing objects: {', '.join(sorted(missing)[:5])}")
    return artifacts, inventory


_ZIP_MAGIC = b"PK\x03\x04"


def validate_derived(out_dir: Path, filename: str, kind: str, *, max_bytes: int) -> Artifact:
    """Render / export outputs: one file, bounded size, matching signature."""
    path = out_dir / filename
    if not path.is_file():
        raise ArtifactError("missing_artifact", f"Blender did not produce {filename}")
    size = path.stat().st_size
    if size == 0 or size > max_bytes:
        raise ArtifactError("artifact_size", f"{filename} has invalid size {size}")
    head = path.read_bytes()[:84]
    if filename.endswith(".png") and head[:8] != _PNG_MAGIC:
        raise ArtifactError("invalid_render", f"{filename} is not a PNG")
    if filename.endswith(".zip") and head[:4] != _ZIP_MAGIC:
        raise ArtifactError("invalid_export", f"{filename} is not a zip archive")
    if filename.endswith(".stl"):
        # binary STL: 80-byte header, uint32 triangle count, 50 bytes per triangle
        triangles = struct.unpack("<I", head[80:84])[0] if len(head) >= 84 else -1
        if triangles <= 0 or size != 84 + 50 * triangles:
            raise ArtifactError("invalid_export", "STL file is empty or truncated")
    return Artifact(kind=kind, filename=filename, size=size, sha256=sha256_file(path))  # type: ignore[arg-type]
