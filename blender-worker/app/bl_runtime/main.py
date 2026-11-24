"""Blender entrypoint.

    blender --background --factory-startup --disable-autoexec --python-exit-code 4 \
            --python bl_runtime/main.py -- --job /work/<job_id>/runtime_job.json

runtime_job.json (written by the trusted worker service):
    {"project_id", "target_version_number", "source_blend": abs path | null,
     "output_dir": abs path, "operations": [...], "render_thumbnail": bool,
     "thumbnail_size": int, "limits": {"max_vertices_per_object", "max_scene_vertices"}}

Writes <output_dir>/result.json. Progress lines on stdout: `F3D_EVENT {json}`.
Exit codes: 0 success, 3 operation/validation failure, 4 unexpected crash.
"""

from __future__ import annotations

import json
import os
import sys
import time
import traceback

# Make sibling modules importable when run as a script by Blender.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import bpy  # noqa: E402
from bl_runtime import export_manager, mesh_validator, render_manager, scene_inspector, scene_manager  # noqa: E402
from bl_runtime.context import RuntimeContext  # noqa: E402
from bl_runtime.operation_executor import ExecutionFailed, execute  # noqa: E402

EXIT_OK = 0
EXIT_FAILED = 3


def emit(stage: str, message: str = "", **data) -> None:
    print("F3D_EVENT " + json.dumps({"stage": stage, "message": message, "data": data}), flush=True)


def _inside(path: str, root: str) -> bool:
    path, root = os.path.realpath(path), os.path.realpath(root)
    return os.path.commonpath([path, root]) == root


def _parse_args(argv: list[str]) -> str:
    args = argv[argv.index("--") + 1 :] if "--" in argv else []
    if len(args) != 2 or args[0] != "--job":
        raise SystemExit("usage: ... -- --job <runtime_job.json>")
    return args[1]


def run(job_path: str) -> int:
    started = time.monotonic()
    with open(job_path, encoding="utf-8") as fh:
        job = json.load(fh)
    workspace = os.path.dirname(os.path.realpath(job_path))
    out_dir = job["output_dir"]
    source = job.get("source_blend")
    if not _inside(out_dir, workspace) or (source and not _inside(source, workspace)):
        raise SystemExit("job paths must be inside the job workspace")
    os.makedirs(out_dir, exist_ok=True)
    result: dict = {"success": False, "blender_version": bpy.app.version_string, "timings_ms": {}}

    def finish(code: int) -> int:
        result["timings_ms"]["total"] = int((time.monotonic() - started) * 1000)
        with open(os.path.join(out_dir, "result.json"), "w", encoding="utf-8") as fh:
            json.dump(result, fh)
        return code

    def timed(name: str, t0: float) -> None:
        result["timings_ms"][name] = int((time.monotonic() - t0) * 1000)

    t0 = time.monotonic()
    emit("loading", "Loading scene" if source else "Creating new scene")
    if source:
        scene_manager.load_blend(source)
    else:
        scene_manager.init_empty_scene()
    timed("load", t0)

    ctx = RuntimeContext(project_id=str(job["project_id"]), version_number=int(job["target_version_number"]))
    ops = job["operations"]
    emit("executing", f"Executing {len(ops)} operations", total=len(ops))
    t0 = time.monotonic()
    try:
        execute(
            ctx,
            ops,
            on_progress=lambda i, n, op: emit(
                "operation", op.get("operation_type", ""), index=i, total=n, operation_id=op.get("operation_id")
            ),
        )
    except ExecutionFailed as exc:
        result.update(failed_operation_id=exc.operation_id, error_code=exc.code, error_message=exc.message)
        emit("failed", exc.message, operation_id=exc.operation_id)
        return finish(EXIT_FAILED)
    timed("execute", t0)

    emit("validating", "Running structural mesh checks")
    limits = job.get("limits") or {}
    errors, warnings = mesh_validator.validate_meshes(
        int(limits.get("max_vertices_per_object", 200_000)), int(limits.get("max_scene_vertices", 1_000_000))
    )
    result["mesh_warnings"] = warnings
    if errors:
        result.update(
            error_code="mesh_validation_failed",
            error_message="; ".join(e["message"] for e in errors[:5]),
            mesh_errors=errors,
        )
        return finish(EXIT_FAILED)

    emit("inspecting", "Inspecting result")
    inventory = scene_inspector.inspect_scene()
    with open(os.path.join(out_dir, "inventory.json"), "w", encoding="utf-8") as fh:
        json.dump(inventory, fh, indent=2, sort_keys=True)
    result["inventory"] = inventory
    result["touched_object_ids"] = sorted(ctx.touched)

    t0 = time.monotonic()
    emit("saving", "Saving .blend")
    export_manager.save_blend(os.path.join(out_dir, "scene.blend"))
    emit("exporting", "Exporting GLB")
    export_manager.export_glb(os.path.join(out_dir, "scene.glb"))
    timed("save_export", t0)

    if job.get("render_thumbnail", True):
        t0 = time.monotonic()
        emit("rendering", "Generating preview")
        render_manager.render_thumbnail(os.path.join(out_dir, "thumbnail.png"), int(job.get("thumbnail_size", 512)))
        timed("render", t0)

    result["success"] = True
    emit("done", "Blender job finished")
    return finish(EXIT_OK)


def main() -> None:
    job_path = _parse_args(sys.argv)
    try:
        code = run(job_path)
    except SystemExit:
        raise
    except Exception:
        traceback.print_exc()
        sys.stdout.flush()
        os._exit(4)
    sys.stdout.flush()
    # os._exit: Blender otherwise ignores the script's return value in background mode.
    os._exit(code)


if __name__ == "__main__":
    main()
