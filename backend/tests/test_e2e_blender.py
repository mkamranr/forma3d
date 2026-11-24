"""Backend + real Blender worker runtime (skipped unless BLENDER_BIN points at Blender 4.5).

The worker code runs in a subprocess (`python -m app.run_once`) because both services use the
top-level package name `app`."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest
from httpx import AsyncClient

from app.core.config import Settings
from forma3d_shared.examples import black_metal_legs_batch, desk_batch
from tests.conftest import auth, register
from tests.test_modeling_pipeline import INTERNAL, FakeWorker, settings  # noqa: F401  (fixture reuse)

BLENDER_BIN = os.environ.get("BLENDER_BIN", "")
WORKER_DIR = Path(__file__).resolve().parents[2] / "blender-worker"

pytestmark = pytest.mark.skipif(not (BLENDER_BIN and Path(BLENDER_BIN).exists()), reason="BLENDER_BIN not set")


def _run_real_worker(spec_json: str, data_root: str, tmp: Path) -> str:
    tmp.mkdir(parents=True, exist_ok=True)
    spec_file = tmp / "spec.json"
    spec_file.write_text(spec_json, encoding="utf-8")
    env = {
        **os.environ,
        "DATA_ROOT": data_root,
        "WORK_ROOT": str(tmp / "work"),
        "BLENDER_BIN": BLENDER_BIN,
        "WORKER_ID": "e2e",
    }
    proc = subprocess.run(
        [sys.executable, "-m", "app.run_once", str(spec_file)],
        cwd=WORKER_DIR,
        env=env,
        capture_output=True,
        text=True,
        timeout=600,
    )
    assert proc.returncode == 0, proc.stderr[-3000:] + proc.stdout[-2000:]
    return proc.stdout


async def _cycle(client: AsyncClient, worker: FakeWorker, tmp: Path, data_root: str) -> dict:
    spec = await worker.next_spec()
    res = await client.post(f"/internal/v1/jobs/{spec.job_id}/start", json={"worker_id": "e2e"}, headers=INTERNAL)
    assert res.json()["proceed"]
    result = _run_real_worker(spec.model_dump_json(), data_root, tmp)
    res = await client.post(
        f"/internal/v1/jobs/{spec.job_id}/complete",
        content=result,
        headers={**INTERNAL, "Content-Type": "application/json"},
    )
    assert res.status_code == 200, res.text
    return res.json()


async def test_desk_then_black_legs_with_real_blender(client: AsyncClient, settings: Settings, tmp_path: Path) -> None:  # noqa: F811
    h = auth((await register(client))["access_token"])
    pid = (await client.post("/api/v1/projects", json={"name": "Desk"}, headers=h)).json()["id"]
    worker = FakeWorker(client, settings.data_root)

    assert (
        await client.post(f"/api/v1/projects/{pid}/modeling/execute", json=desk_batch(), headers=h)
    ).status_code == 202
    v1 = await _cycle(client, worker, tmp_path / "j1", settings.data_root)
    assert v1["status"] == "succeeded", v1

    assert (
        await client.post(f"/api/v1/projects/{pid}/modeling/execute", json=black_metal_legs_batch(), headers=h)
    ).status_code == 202
    v2 = await _cycle(client, worker, tmp_path / "j2", settings.data_root)
    assert v2["status"] == "succeeded", v2

    versions = (await client.get(f"/api/v1/projects/{pid}/versions", headers=h)).json()
    assert [v["version_number"] for v in versions] == [2, 1]
    d1 = (await client.get(f"/api/v1/projects/{pid}/versions/{versions[1]['id']}", headers=h)).json()["inventory"]
    d2 = (await client.get(f"/api/v1/projects/{pid}/versions/{versions[0]['id']}", headers=h)).json()["inventory"]
    o1 = {o["object_id"]: o for o in d1["objects"]}
    o2 = {o["object_id"]: o for o in d2["objects"]}
    assert set(o1) == set(o2)
    assert o2["leg_fl"]["material_ids"] == ["black_metal"]
    assert o2["desk_top"]["material_ids"] == o1["desk_top"]["material_ids"] == ["oak"]
    assert o2["desk_top"]["dimensions"] == o1["desk_top"]["dimensions"]

    glb = await client.get(f"/api/v1/projects/{pid}/versions/{versions[0]['id']}/assets/glb", headers=h)
    assert glb.status_code == 200 and glb.content[:4] == b"glTF"
    assert (await client.get(f"/api/v1/projects/{pid}/thumbnail", headers=h)).content[:4] == b"\x89PNG"
