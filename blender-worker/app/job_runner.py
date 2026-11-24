"""Runs one Blender job end-to-end (spec §9.2 lifecycle) and returns a structured result.

The committed version is never modified: Blender works on a copy inside an isolated job
workspace, and only fully validated outputs are published (atomically) to the staging directory
that the backend later promotes into an immutable version.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
from collections.abc import Callable
from pathlib import Path

from forma3d_shared.schemas.job import ARTIFACT_FILENAMES, BlenderJobResult, BlenderJobSpec
from forma3d_shared.schemas.modeling_operation import OperationBatch
from pydantic import ValidationError

from .artifacts import ArtifactError, sha256_file, validate_outputs
from .blender_runner import blender_command, redact, run_blender
from .config import WorkerConfig

FRIENDLY = {
    "timeout": "Building the model took too long and was stopped. Try a simpler request.",
    "cancelled": "The job was cancelled.",
    "crash": "Blender stopped unexpectedly while building the model.",
    "source_missing": "The previous version of this project could not be found.",
    "source_checksum_mismatch": "The previous version failed an integrity check.",
    "invalid_job": "The job request was invalid.",
}


class JobError(Exception):
    def __init__(self, code: str, message: str | None = None, failed_operation_id: str | None = None) -> None:
        super().__init__(message or FRIENDLY.get(code, code))
        self.code = code
        self.message = message or FRIENDLY.get(code, code)
        self.failed_operation_id = failed_operation_id


def batch_digest(operations: list[dict]) -> str:
    return hashlib.sha256(
        json.dumps({"schema_version": "1.0", "operations": operations}, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def resolve_data_path(data_root: Path, relative: str, *, must_start: str = "projects/") -> Path:
    """Map a backend-supplied relative path into DATA_ROOT; refuse anything that escapes it."""
    if (
        not relative.startswith(must_start)
        or os.path.isabs(relative)
        or "\x00" in relative
        or ".." in Path(relative).parts
    ):
        raise JobError("invalid_job", "invalid storage path in job")
    root = data_root.resolve()
    candidate = (root / relative).resolve()
    if root not in candidate.parents:
        raise JobError("invalid_job", "storage path escapes data root")
    return candidate


def _publish(out_dir: Path, staging: Path, artifacts_filenames: list[str]) -> None:
    """Copy validated outputs into staging atomically (tmp dir + rename)."""
    staging.parent.mkdir(parents=True, exist_ok=True)
    partial = staging.with_name(staging.name + ".partial")
    shutil.rmtree(partial, ignore_errors=True)
    if staging.exists():
        shutil.rmtree(staging)  # leftover from a previous attempt of the same job
    partial.mkdir()
    for name in artifacts_filenames:
        shutil.copy2(out_dir / name, partial / name)
        with (partial / name).open("r+b") as fh:  # fsync needs a writable handle on Windows
            os.fsync(fh.fileno())
    os.replace(partial, staging)


def execute_job(
    spec: BlenderJobSpec,
    cfg: WorkerConfig,
    *,
    emit: Callable[[str, str, dict], None] = lambda _s, _m, _d: None,
    is_cancelled: Callable[[], bool] = lambda: False,
) -> BlenderJobResult:
    workspace = cfg.work_root / str(spec.job_id)
    result = BlenderJobResult(job_id=spec.job_id, success=False, worker_id=cfg.worker_id)
    keep = False
    try:
        # 1. Integrity + structural re-validation (defense in depth; backend already validated).
        if batch_digest(spec.operations) != spec.batch_sha256:
            raise JobError("invalid_job", "operation batch hash mismatch")
        try:
            OperationBatch.model_validate({"schema_version": "1.0", "operations": spec.operations})
        except ValidationError as exc:
            raise JobError(
                "invalid_job", f"operation batch failed schema validation: {exc.errors()[0]['msg']}"
            ) from exc
        staging = resolve_data_path(cfg.data_root, spec.staging_dir)

        # 2. Isolated workspace
        shutil.rmtree(workspace, ignore_errors=True)
        (workspace / "out").mkdir(parents=True)

        # 3. Source version (verified copy)
        source_blend = None
        if spec.source is not None:
            src = resolve_data_path(cfg.data_root, spec.source.blend_path)
            if not src.is_file():
                raise JobError("source_missing")
            if sha256_file(src) != spec.source.blend_sha256:
                raise JobError("source_checksum_mismatch")
            source_blend = workspace / "source.blend"
            shutil.copy2(src, source_blend)

        # 4. Runtime job file for Blender
        job_file = workspace / "runtime_job.json"
        job_file.write_text(
            json.dumps(
                {
                    "project_id": str(spec.project_id),
                    "target_version_number": spec.target_version_number,
                    "source_blend": str(source_blend) if source_blend else None,
                    "output_dir": str(workspace / "out"),
                    "operations": spec.operations,
                    "render_thumbnail": spec.render_thumbnail,
                    "thumbnail_size": min(spec.thumbnail_size, cfg.limits.max_thumbnail_size),
                    "limits": {
                        "max_vertices_per_object": cfg.limits.max_vertices_per_object,
                        "max_scene_vertices": cfg.limits.max_scene_vertices,
                    },
                }
            ),
            encoding="utf-8",
        )

        # 5. Run Blender
        env = dict(os.environ)
        env.setdefault("BLENDER_USER_CONFIG", str(workspace / ".blender-config"))
        env.setdefault("BLENDER_USER_SCRIPTS", str(workspace / ".blender-scripts"))
        run = run_blender(
            blender_command(cfg.blender_bin, cfg.runtime_main, job_file),
            timeout_seconds=min(spec.timeout_seconds, cfg.limits.job_timeout_seconds),
            is_cancelled=is_cancelled,
            on_event=lambda e: emit(str(e.get("stage", "")), str(e.get("message", "")), e.get("data") or {}),
            grace_seconds=cfg.terminate_grace_seconds,
            redact_paths=(workspace, cfg.data_root),
            env=env,
        )
        result.duration_ms = run.duration_ms
        result.log_tail = run.log_tail
        if run.cancelled:
            raise JobError("cancelled")
        if run.timed_out:
            raise JobError("timeout")

        out_dir = workspace / "out"
        runtime_result: dict = {}
        if (out_dir / "result.json").is_file():
            runtime_result = json.loads((out_dir / "result.json").read_text(encoding="utf-8"))
            result.blender_version = runtime_result.get("blender_version")
        if run.exit_code != 0 or not runtime_result.get("success"):
            if runtime_result.get("error_code"):
                raise JobError(
                    runtime_result["error_code"],
                    runtime_result.get("error_message"),
                    runtime_result.get("failed_operation_id"),
                )
            raise JobError("crash", f"{FRIENDLY['crash']} (exit code {run.exit_code})")

        # 6. Validate outputs, add operations.json, publish to staging
        (out_dir / ARTIFACT_FILENAMES["operations"]).write_text(
            json.dumps({"schema_version": "1.0", "operations": spec.operations}, indent=2), encoding="utf-8"
        )
        try:
            artifacts, inventory = validate_outputs(
                out_dir, expect_thumbnail=spec.render_thumbnail, max_bytes=cfg.limits.max_output_file_bytes
            )
        except ArtifactError as exc:
            raise JobError(exc.code, exc.message) from exc
        _publish(out_dir, staging, [a.filename for a in artifacts])

        result.success = True
        result.artifacts = artifacts
        result.inventory = inventory
        return result

    except JobError as exc:
        keep = cfg.keep_failed_workspaces
        result.success = False
        result.error_code = exc.code
        result.error_message = exc.message
        result.failed_operation_id = exc.failed_operation_id
        return result
    except Exception as exc:  # never let an unexpected error escape without a structured result
        keep = cfg.keep_failed_workspaces
        result.success = False
        result.error_code = "worker_error"
        result.error_message = f"Unexpected worker error: {type(exc).__name__}"
        detail = redact(f"{type(exc).__name__}: {exc}", workspace, cfg.data_root)
        result.log_tail = f"{result.log_tail}\n{detail}"[-4000:]
        return result
    finally:
        if not keep:
            shutil.rmtree(workspace, ignore_errors=True)
