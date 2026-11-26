"""Run the whole stack on one machine without Docker, PostgreSQL or a real Redis.

    BLENDER_BIN=/path/to/blender backend/.venv/Scripts/python deployment/scripts/dev_local.py
    cd frontend && npm run dev        # http://localhost:5173 (proxies /api to :8000)

- redis:    fakeredis TCP server on 127.0.0.1:$DEV_REDIS_PORT (default 6390, in memory)
- backend:  the real FastAPI app on http://127.0.0.1:$DEV_PORT (default 8000), SQLite in DEV_DATA_DIR
- workers:  the real blender-worker (`python -m app.worker`) and agent-worker (`python -m app.main`)
            as subprocesses, talking to the backend's internal API exactly as in Docker

The agent uses the deterministic FakeProvider unless LLM_PROVIDER / LLM_BASE_URL are set, e.g.
    LLM_PROVIDER=vllm LLM_BASE_URL=http://gpu-box:8000/v1 LLM_MODEL=Qwen/Qwen2.5-32B-Instruct ...

For development and demos only.
"""

from __future__ import annotations

import asyncio
import os
import subprocess
import sys
import tempfile
import threading
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))

import app.models  # noqa: F401
import uvicorn
from app.core.config import Settings
from app.database.base import Base
from app.database.session import Database
from app.main import create_app
from app.services.rate_limit import MemoryRateLimiter
from fakeredis import TcpFakeServer

TOKEN = "dev-local-internal-token"
PORT = int(os.environ.get("DEV_PORT", "8000"))
REDIS_PORT = int(os.environ.get("DEV_REDIS_PORT", "6390"))


def start_redis() -> str:
    server = TcpFakeServer(("127.0.0.1", REDIS_PORT), server_type="redis")
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return f"redis://127.0.0.1:{REDIS_PORT}/0"


def start_worker(name: str, module: str, env: dict[str, str]) -> subprocess.Popen:
    proc = subprocess.Popen(
        [sys.executable, "-u", "-m", module], cwd=ROOT / name, env=env,
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding="utf-8", errors="replace",
    )

    def pump() -> None:
        assert proc.stdout is not None
        for line in proc.stdout:
            print(f"[{name}] {line.rstrip()}", flush=True)

    threading.Thread(target=pump, daemon=True).start()
    return proc


def main() -> None:
    base = Path(os.environ.get("DEV_DATA_DIR") or Path(tempfile.gettempdir()) / "forma3d-dev")
    data_root, work_root = base / "data", base / "work"
    data_root.mkdir(parents=True, exist_ok=True)
    work_root.mkdir(parents=True, exist_ok=True)
    db_url = f"sqlite+aiosqlite:///{(base / 'forma3d.db').as_posix()}"
    redis_url = start_redis()

    settings = Settings(
        app_env="development",
        database_url=db_url,
        redis_url=redis_url,
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
            # create_all never alters existing tables: add columns introduced since the data dir was made.
            from sqlalchemy import text

            cols = {r[1] for r in (await conn.execute(text("PRAGMA table_info(users)"))).all()}
            if "is_admin" not in cols:
                await conn.execute(text("ALTER TABLE users ADD COLUMN is_admin BOOLEAN NOT NULL DEFAULT 0"))
            runs = {r[1] for r in (await conn.execute(text("PRAGMA table_info(agent_runs)"))).all()}
            for col, ddl in (
                ("kind", "VARCHAR(16) NOT NULL DEFAULT 'agent'"),
                ("node", "VARCHAR(64)"),
                ("seq", "INTEGER"),
                ("debug", "JSON"),
            ):
                if col not in runs:
                    await conn.execute(text(f"ALTER TABLE agent_runs ADD COLUMN {col} {ddl}"))

    asyncio.run(init_db())
    db = Database(db_url)  # fresh engine for uvicorn's event loop

    common = {
        **os.environ,
        "REDIS_URL": redis_url,
        "BACKEND_INTERNAL_URL": f"http://127.0.0.1:{PORT}",
        "INTERNAL_SERVICE_TOKEN": TOKEN,
        "PYTHONIOENCODING": "utf-8",
    }
    if not os.environ.get("LLM_PROVIDER"):
        common["LLM_PROVIDER"] = "vllm" if os.environ.get("LLM_BASE_URL") else "fake"
    workers = [
        start_worker("blender-worker", "app.worker", {
            **common, "DATA_ROOT": str(data_root), "WORK_ROOT": str(work_root), "WORKER_ID": "blender-dev",
            "LIVENESS_FILE": str(base / "blender-alive"),
        }),
        start_worker("agent-worker", "app.main", {
            **common, "WORKER_ID": "agent-dev", "LIVENESS_FILE": str(base / "agent-alive"),
            "CHECKPOINT_URL": os.environ.get("CHECKPOINT_URL") or f"sqlite:///{(base / 'checkpoints.db').as_posix()}",
        }),
    ]
    print(f"[dev] data in {base}; redis {redis_url}; LLM provider {common['LLM_PROVIDER']}", flush=True)
    print(f"[dev] blender: {os.environ.get('BLENDER_BIN') or '(BLENDER_BIN not set!)'}", flush=True)
    application = create_app(settings, db=db, rate_limiter=MemoryRateLimiter())
    try:
        uvicorn.run(application, host="127.0.0.1", port=PORT, log_level="warning")
    finally:
        for w in workers:
            w.terminate()
        for w in workers:
            try:
                w.wait(timeout=10)
            except subprocess.TimeoutExpired:
                w.kill()


if __name__ == "__main__":
    main()
