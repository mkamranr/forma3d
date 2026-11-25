"""Run the whole modeling stack on one machine without Docker, PostgreSQL or Redis.

    BLENDER_BIN=/path/to/blender backend/.venv/Scripts/python deployment/scripts/dev_local.py
    cd frontend && npm run dev        # http://localhost:5173 (proxies /api to :8000)

- backend: the real FastAPI app on http://127.0.0.1:8000, SQLite (file in DEV_DATA_DIR), in-process
  fakeredis shared with the worker thread
- worker: a thread that takes jobs off `jobs:blender`, calls the real internal start/complete
  endpoints and runs the real Blender runtime via `python -m app.run_once`

For development and demos only (single process, no auth hardening, no persistence of Redis).
"""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))

import app.models  # noqa: F401
import fakeredis
import httpx
import uvicorn
from app.core.config import Settings
from app.database.base import Base
from app.database.session import Database
from app.main import create_app
from app.services.rate_limit import MemoryRateLimiter
from forma3d_shared.schemas.job import BLENDER_JOB_STREAM

TOKEN = "dev-local-internal-token"
PORT = int(os.environ.get("DEV_PORT", "8000"))


def worker_loop(server: fakeredis.FakeServer, data_root: Path, work_root: Path) -> None:
    redis = fakeredis.FakeRedis(server=server)
    http = httpx.Client(base_url=f"http://127.0.0.1:{PORT}", headers={"X-Internal-Token": TOKEN}, timeout=60)
    last = "0-0"
    blender = os.environ.get("BLENDER_BIN", "")
    print(f"[worker] using Blender at {blender or '(BLENDER_BIN not set!)'}", flush=True)
    while True:
        entries = redis.xread({BLENDER_JOB_STREAM: last}, count=1, block=1000) or []
        for _stream, items in entries:
            for entry_id, fields in items:
                last = entry_id.decode() if isinstance(entry_id, bytes) else entry_id
                spec = json.loads(fields[b"job"])
                job_id = spec["job_id"]
                try:
                    start = http.post(f"/internal/v1/jobs/{job_id}/start", json={"worker_id": "dev-local"}).json()
                    if not start.get("proceed"):
                        print(f"[worker] skip {job_id}: {start.get('reason')}", flush=True)
                        continue
                    spec_file = work_root / f"{job_id}.json"
                    spec_file.write_text(json.dumps(spec), encoding="utf-8")
                    t0 = time.time()
                    env = {
                        **os.environ,
                        "DATA_ROOT": str(data_root),
                        "WORK_ROOT": str(work_root),
                        "WORKER_ID": "dev-local",
                    }
                    proc = subprocess.run(
                        [sys.executable, "-m", "app.run_once", str(spec_file)],
                        cwd=ROOT / "blender-worker",
                        env=env,
                        capture_output=True,
                        check=False,
                        text=True,
                        timeout=900,
                    )
                    result = proc.stdout or json.dumps(
                        {
                            "job_id": job_id,
                            "success": False,
                            "error_code": "worker_error",
                            "error_message": proc.stderr[-500:] or "worker crashed",
                        }
                    )
                    res = http.post(
                        f"/internal/v1/jobs/{job_id}/complete",
                        content=result,
                        headers={"Content-Type": "application/json"},
                    )
                    print(f"[worker] {job_id} -> {res.json().get('status')} in {time.time() - t0:.1f}s", flush=True)
                except Exception as exc:  # keep the loop alive in dev
                    print(f"[worker] error on {job_id}: {exc!r}", flush=True)


def main() -> None:
    base = Path(os.environ.get("DEV_DATA_DIR") or Path(tempfile.gettempdir()) / "forma3d-dev")
    data_root, work_root = base / "data", base / "work"
    data_root.mkdir(parents=True, exist_ok=True)
    work_root.mkdir(parents=True, exist_ok=True)
    db_url = f"sqlite+aiosqlite:///{(base / 'forma3d.db').as_posix()}"

    settings = Settings(
        app_env="development",
        database_url=db_url,
        jwt_secret=os.environ.get("JWT_SECRET", "dev-local-secret-dev-local-secret-0123"),
        internal_service_token=TOKEN,
        data_root=str(data_root),
        cookie_secure=False,
        rate_limit_auth_per_minute=1000,
        rate_limit_jobs_per_minute=1000,
    )
    db = Database(db_url)

    async def init_db() -> None:
        async with db.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

    asyncio.run(init_db())
    db = Database(db_url)  # fresh engine for uvicorn's event loop

    server = fakeredis.FakeServer()
    application = create_app(
        settings, db=db, redis=fakeredis.FakeAsyncRedis(server=server), rate_limiter=MemoryRateLimiter()
    )
    threading.Thread(target=worker_loop, args=(server, data_root, work_root), daemon=True).start()
    print(f"[dev] data in {base}", flush=True)
    uvicorn.run(application, host="127.0.0.1", port=PORT, log_level="warning")


if __name__ == "__main__":
    main()
