"""Run a single job spec without Redis or the backend (debugging and end-to-end tests).

    python -m app.run_once spec.json > result.json

Configuration comes from the same environment variables as the worker (DATA_ROOT, WORK_ROOT,
BLENDER_BIN, ...). Prints the BlenderJobResult JSON on stdout; progress goes to stderr."""

from __future__ import annotations

import sys
from pathlib import Path

from forma3d_shared.schemas.job import BlenderJobSpec

from app.config import WorkerConfig
from app.job_runner import execute_job


def main(argv: list[str]) -> int:
    if len(argv) != 1:
        print("usage: python -m app.run_once SPEC.json", file=sys.stderr)
        return 2
    spec = BlenderJobSpec.model_validate_json(Path(argv[0]).read_text(encoding="utf-8"))
    cfg = WorkerConfig.from_env()
    result = execute_job(spec, cfg, emit=lambda stage, msg, _d: print(f"[{stage}] {msg}", file=sys.stderr))
    sys.stdout.write(result.model_dump_json())
    return 0 if result.success else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
