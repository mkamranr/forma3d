"""Worker configuration from environment variables."""

from __future__ import annotations

import os
import socket
from dataclasses import dataclass, field
from pathlib import Path

from forma3d_shared.constants.limits import ModelingLimits

APP_DIR = Path(__file__).resolve().parent


@dataclass(frozen=True)
class WorkerConfig:
    redis_url: str = "redis://redis:6379/0"
    backend_url: str = "http://backend:8000"
    internal_token: str = "change-me-internal"
    data_root: Path = Path("/data")
    work_root: Path = Path("/tmp/forma3d-jobs")
    blender_bin: str = "/opt/blender/blender"
    runtime_main: Path = APP_DIR / "bl_runtime" / "main.py"
    worker_id: str = field(default_factory=lambda: f"blender-{socket.gethostname()}")
    limits: ModelingLimits = field(default_factory=ModelingLimits)
    keep_failed_workspaces: bool = False
    terminate_grace_seconds: float = 5.0
    liveness_file: Path = Path("/tmp/worker-alive")

    @classmethod
    def from_env(cls) -> WorkerConfig:
        env = os.environ.get
        return cls(
            redis_url=env("REDIS_URL", cls.redis_url),
            backend_url=env("BACKEND_INTERNAL_URL", cls.backend_url),
            internal_token=env("INTERNAL_SERVICE_TOKEN", cls.internal_token),
            data_root=Path(env("DATA_ROOT", "/data")),
            work_root=Path(env("WORK_ROOT", "/tmp/forma3d-jobs")),
            blender_bin=env("BLENDER_BIN", cls.blender_bin),
            worker_id=env("WORKER_ID", f"blender-{socket.gethostname()}"),
            limits=ModelingLimits.from_env(),
            keep_failed_workspaces=env("KEEP_FAILED_WORKSPACES", "false").lower() == "true",
            liveness_file=Path(env("LIVENESS_FILE", "/tmp/worker-alive")),
        )
