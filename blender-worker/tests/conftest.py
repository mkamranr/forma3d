from __future__ import annotations

import os
import uuid
from pathlib import Path

import pytest
from forma3d_shared.constants.limits import ModelingLimits
from forma3d_shared.schemas.job import BlenderJobSpec, SourceVersion
from forma3d_shared.validation import validate_batch

from app.config import WorkerConfig
from app.job_runner import batch_digest

BLENDER_BIN = os.environ.get("BLENDER_BIN")
requires_blender = pytest.mark.skipif(not BLENDER_BIN, reason="set BLENDER_BIN to run Blender integration tests")

USER = uuid.UUID("00000000-0000-4000-8000-000000000001")
PROJECT = uuid.UUID("00000000-0000-4000-8000-000000000002")


@pytest.fixture
def cfg(tmp_path: Path) -> WorkerConfig:
    return WorkerConfig(
        data_root=tmp_path / "data",
        work_root=tmp_path / "work",
        blender_bin=BLENDER_BIN or "blender-not-installed",
        worker_id="test-worker",
        limits=ModelingLimits(job_timeout_seconds=240),
        liveness_file=tmp_path / "alive",
    )


def make_spec(
    operations: list[dict],
    *,
    version: int = 1,
    source: SourceVersion | None = None,
    thumbnail: bool = False,
    job_id: uuid.UUID | None = None,
    timeout: int = 240,
) -> BlenderJobSpec:
    job_id = job_id or uuid.uuid4()
    return BlenderJobSpec(
        job_id=job_id,
        project_id=PROJECT,
        user_id=USER,
        operation_batch_id=uuid.uuid4(),
        target_version_number=version,
        source=source,
        staging_dir=f"projects/{USER}/{PROJECT}/staging/{job_id}",
        operations=operations,
        batch_sha256=batch_digest(operations),
        render_thumbnail=thumbnail,
        thumbnail_size=128,
        timeout_seconds=timeout,
    )


def canonical(batch: dict, inventory=None) -> list[dict]:
    return list(validate_batch(batch, inventory).canonical_operations)
