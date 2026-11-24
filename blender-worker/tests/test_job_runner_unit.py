"""Job runner guards that need no Blender binary."""

from __future__ import annotations

import uuid

import pytest
from forma3d_shared.examples import desk_batch
from forma3d_shared.schemas.job import SourceVersion

from app.job_runner import JobError, execute_job, resolve_data_path
from tests.conftest import canonical, make_spec


def test_hash_mismatch_is_rejected_before_blender(cfg):
    spec = make_spec(canonical(desk_batch()))
    tampered = spec.model_copy(update={"operations": [*spec.operations[:-1]]})
    result = execute_job(tampered, cfg)
    assert not result.success and result.error_code == "invalid_job"
    assert "hash" in result.error_message


def test_schema_invalid_operations_rejected(cfg):
    ops = [{"operation_id": "x", "operation_type": "exec_python", "parameters": {"code": "1"}}]
    result = execute_job(make_spec(ops), cfg)
    assert result.error_code == "invalid_job"


@pytest.mark.parametrize("rel", ["../etc/passwd", "projects/../../etc", "/etc/passwd", "tmp/x"])
def test_storage_paths_are_confined(cfg, rel):
    with pytest.raises(JobError):
        resolve_data_path(cfg.data_root, rel)


def test_missing_source_version_fails_cleanly(cfg):
    src = SourceVersion(
        version_id=uuid.uuid4(),
        version_number=1,
        blend_path="projects/u/p/versions/v0001/scene.blend",
        blend_sha256="0" * 64,
    )
    result = execute_job(make_spec(canonical(desk_batch()), source=src, version=2), cfg)
    assert result.error_code == "source_missing"
    assert not (cfg.work_root / str(result.job_id)).exists()  # workspace cleaned up


def test_checksum_mismatch_on_source(cfg):
    rel = "projects/u/p/versions/v0001/scene.blend"
    path = cfg.data_root / rel
    path.parent.mkdir(parents=True)
    path.write_bytes(b"BLENDER-v405 fake")
    src = SourceVersion(version_id=uuid.uuid4(), version_number=1, blend_path=rel, blend_sha256="0" * 64)
    result = execute_job(make_spec(canonical(desk_batch()), source=src, version=2), cfg)
    assert result.error_code == "source_checksum_mismatch"


def test_missing_blender_binary_reports_worker_error(cfg):
    result = execute_job(
        make_spec(canonical(desk_batch())), cfg.__class__(**{**cfg.__dict__, "blender_bin": "no-such-blender-binary"})
    )
    assert not result.success and result.error_code == "worker_error"
