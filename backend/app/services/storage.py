"""Project asset storage abstraction (filesystem for the MVP, S3-compatible later).

All paths are generated server-side from ids. Callers never pass free-form paths;
`resolve()` additionally guards against traversal for any stored relative path."""

from __future__ import annotations

import os
import uuid
from pathlib import Path


class StoragePathError(ValueError):
    pass


class ProjectStorage:
    def __init__(self, data_root: str | os.PathLike[str]) -> None:
        self.root = Path(data_root).resolve()

    def project_dir(self, user_id: uuid.UUID, project_id: uuid.UUID) -> str:
        # Round-trip through UUID guarantees no path characters.
        return f"projects/{uuid.UUID(str(user_id))}/{uuid.UUID(str(project_id))}"

    def version_dir(self, user_id: uuid.UUID, project_id: uuid.UUID, version_number: int) -> str:
        if not 1 <= int(version_number) <= 99_999:
            raise StoragePathError("version number out of range")
        return f"{self.project_dir(user_id, project_id)}/versions/v{int(version_number):04d}"

    def staging_dir(self, user_id: uuid.UUID, project_id: uuid.UUID, job_id: uuid.UUID) -> str:
        return f"{self.project_dir(user_id, project_id)}/staging/{uuid.UUID(str(job_id))}"

    def resolve(self, relative: str) -> Path:
        """Map a stored relative path to an absolute path inside DATA_ROOT."""
        if not relative or os.path.isabs(relative) or "\x00" in relative:
            raise StoragePathError("invalid storage path")
        candidate = (self.root / relative).resolve()
        if candidate != self.root and self.root not in candidate.parents:
            raise StoragePathError("path escapes storage root")
        return candidate
