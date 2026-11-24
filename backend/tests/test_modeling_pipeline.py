"""End-to-end modeling pipeline with a fake Blender worker (no Blender needed):
submit -> queue -> worker start -> staged artifacts -> complete -> version commit -> SSE/assets."""

from __future__ import annotations

import hashlib
import json
import uuid
from pathlib import Path

import pytest
from httpx import AsyncClient

from app.core.config import Settings
from forma3d_shared.examples import black_metal_legs_batch, desk_batch
from forma3d_shared.schemas.events import cancel_flag_key
from forma3d_shared.schemas.job import ARTIFACT_FILENAMES, BLENDER_JOB_STREAM, BlenderJobSpec
from tests.conftest import auth, register

TOKEN = "internal-test-token"
INTERNAL = {"X-Internal-Token": TOKEN}


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return Settings(
        app_env="test",
        database_url="sqlite+aiosqlite://",
        jwt_secret="test-secret-test-secret-test-secret",
        log_level="WARNING",
        rate_limit_auth_per_minute=1000,
        rate_limit_jobs_per_minute=1000,
        internal_service_token=TOKEN,
        data_root=str(tmp_path),
    )


class FakeWorker:
    """Pops jobs from the stream and emulates the Blender worker's staging + callbacks."""

    def __init__(self, client: AsyncClient, data_root: str) -> None:
        self.client = client
        self.root = Path(data_root)
        self.scenes: dict[str, dict] = {}

    async def next_spec(self) -> BlenderJobSpec:
        redis = self.client.app.state.redis  # type: ignore[attr-defined]
        entries = await redis.xrange(BLENDER_JOB_STREAM)
        assert entries, "no job queued"
        entry_id, fields = entries[-1]
        await redis.xdel(BLENDER_JOB_STREAM, entry_id)
        return BlenderJobSpec.model_validate_json(fields[b"job"])

    def _inventory(self, spec: BlenderJobSpec) -> dict:
        scene = json.loads(json.dumps(self.scenes.get(str(spec.project_id), {"objects": [], "materials": []})))
        for op in spec.operations:
            t = op["operation_type"]
            if t.startswith("create_") and op.get("target_object_id"):
                scene["objects"].append(
                    {"object_id": op["target_object_id"], "name": op["parameters"]["name"], "mesh": {"vertices": 8}}
                )
            elif t == "create_material":
                p = op["parameters"]
                scene["materials"].append({"material_id": p["material_id"], "name": p["name"]})
        return scene

    async def run(
        self,
        spec: BlenderJobSpec | None = None,
        *,
        fail: bool = False,
        corrupt: bool = False,
        commit_scene: bool = True,
    ) -> dict:
        spec = spec or await self.next_spec()
        res = await self.client.post(
            f"/internal/v1/jobs/{spec.job_id}/start", json={"worker_id": "fake-1"}, headers=INTERNAL
        )
        assert res.status_code == 200 and res.json()["proceed"], res.text
        if fail:
            body = {
                "job_id": str(spec.job_id),
                "success": False,
                "worker_id": "fake-1",
                "failed_operation_id": spec.operations[-1]["operation_id"],
                "error_code": "operation_failed",
                "error_message": "Operation failed",
            }
        else:
            inventory = self._inventory(spec)
            staging = self.root / spec.staging_dir
            staging.mkdir(parents=True)
            contents = {
                "blend": b"BLENDER-v405" + uuid.uuid4().bytes,
                "glb": b"glTF" + uuid.uuid4().bytes,
                "thumbnail": b"\x89PNG\r\n\x1a\n" + uuid.uuid4().bytes,
                "inventory": json.dumps(inventory).encode(),
                "operations": json.dumps(spec.operations).encode(),
            }
            artifacts = []
            for kind, data in contents.items():
                (staging / ARTIFACT_FILENAMES[kind]).write_bytes(data)
                artifacts.append(
                    {
                        "kind": kind,
                        "filename": ARTIFACT_FILENAMES[kind],
                        "size": len(data),
                        "sha256": hashlib.sha256(b"x" if corrupt and kind == "glb" else data).hexdigest(),
                    }
                )
            body = {
                "job_id": str(spec.job_id),
                "success": True,
                "worker_id": "fake-1",
                "artifacts": artifacts,
                "inventory": inventory,
                "blender_version": "4.5.17",
            }
            if commit_scene:
                self.scenes[str(spec.project_id)] = inventory
        res = await self.client.post(f"/internal/v1/jobs/{spec.job_id}/complete", json=body, headers=INTERNAL)
        assert res.status_code == 200, res.text
        return res.json()


async def _setup(client: AsyncClient, email: str = "a@example.com") -> tuple[dict, str]:
    tokens = await register(client, email=email)
    h = auth(tokens["access_token"])
    res = await client.post("/api/v1/projects", json={"name": "Desk"}, headers=h)
    return h, res.json()["id"]


async def _submit(client: AsyncClient, h: dict, pid: str, batch: dict) -> dict:
    res = await client.post(f"/api/v1/projects/{pid}/modeling/execute", json=batch, headers=h)
    assert res.status_code == 202, res.text
    return res.json()


async def test_full_pipeline_creates_versions(client: AsyncClient, settings: Settings) -> None:
    h, pid = await _setup(client)
    worker = FakeWorker(client, settings.data_root)

    sub = await _submit(client, h, pid, desk_batch())
    assert sub["status"] == "queued"
    job = (await client.get(f"/api/v1/jobs/{sub['job_id']}", headers=h)).json()
    assert job["status"] == "queued"

    done = await worker.run()
    assert done["status"] == "succeeded" and done["version_id"]

    project = (await client.get(f"/api/v1/projects/{pid}", headers=h)).json()
    assert project["current_version_id"] == done["version_id"]
    assert project["thumbnail_url"]

    versions = (await client.get(f"/api/v1/projects/{pid}/versions", headers=h)).json()
    assert [v["version_number"] for v in versions] == [1]
    v1 = versions[0]
    assert v1["is_current"] and v1["object_count"] == 5
    assert {a["kind"] for a in v1["assets"]} == {"blend", "glb", "thumbnail", "inventory", "operations"}
    assert "path" not in json.dumps(v1).lower()  # never leak storage paths

    v1_dir = Path(settings.data_root) / "projects"
    assert list(v1_dir.glob("*/*/versions/v0001/scene.glb"))
    assert not list(v1_dir.glob("*/*/staging/*"))

    glb = await client.get(f"/api/v1/projects/{pid}/versions/{v1['id']}/assets/glb", headers=h)
    assert glb.status_code == 200 and glb.content.startswith(b"glTF")
    assert glb.headers["content-type"] == "model/gltf-binary"
    thumb = await client.get(f"/api/v1/projects/{pid}/thumbnail", headers=h)
    assert thumb.status_code == 200 and thumb.content.startswith(b"\x89PNG")

    detail = (await client.get(f"/api/v1/projects/{pid}/versions/{v1['id']}", headers=h)).json()
    assert {o["object_id"] for o in detail["inventory"]["objects"]} >= {"desk_top", "leg_fl"}

    # Second batch edits the existing scene and is validated against the v1 inventory.
    await _submit(client, h, pid, black_metal_legs_batch())
    spec = await worker.next_spec()
    assert spec.source is not None and spec.source.version_number == 1 and spec.target_version_number == 2
    await worker.run(spec)
    versions = (await client.get(f"/api/v1/projects/{pid}/versions", headers=h)).json()
    assert [v["version_number"] for v in versions] == [2, 1]
    assert versions[0]["parent_version_id"] == v1["id"] and versions[0]["is_current"]


async def test_invalid_batch_rejected_before_queue(client: AsyncClient) -> None:
    h, pid = await _setup(client)
    bad = black_metal_legs_batch()  # targets legs that don't exist in an empty scene
    res = await client.post(f"/api/v1/projects/{pid}/modeling/execute", json=bad, headers=h)
    assert res.status_code == 422
    body = res.json()
    assert body["code"] == "invalid_batch" and any(i["code"] == "unknown_object" for i in body["issues"])
    assert await client.app.state.redis.xlen(BLENDER_JOB_STREAM) == 0  # type: ignore[attr-defined]

    res = await client.post(
        f"/api/v1/projects/{pid}/modeling/execute",
        json={
            "schema_version": "1.0",
            "summary": "x",
            "operations": [{"operation_id": "a", "operation_type": "run_python", "parameters": {"code": "1"}}],
        },
        headers=h,
    )
    assert res.status_code == 422


async def test_project_lock_rejects_concurrent_job(client: AsyncClient, settings: Settings) -> None:
    h, pid = await _setup(client)
    await _submit(client, h, pid, desk_batch())
    res = await client.post(f"/api/v1/projects/{pid}/modeling/execute", json=desk_batch(), headers=h)
    assert res.status_code == 409 and res.json()["code"] == "project_busy"
    await FakeWorker(client, settings.data_root).run()
    # lock released after completion: a fresh valid batch is accepted again
    await _submit(client, h, pid, black_metal_legs_batch())


async def test_failed_job_keeps_head(client: AsyncClient, settings: Settings) -> None:
    h, pid = await _setup(client)
    worker = FakeWorker(client, settings.data_root)
    await _submit(client, h, pid, desk_batch())
    v1 = (await worker.run())["version_id"]

    await _submit(client, h, pid, black_metal_legs_batch())
    failed = await worker.run(fail=True)
    assert failed["status"] == "failed" and failed["error_code"] == "operation_failed"
    assert failed["failed_operation_id"]
    project = (await client.get(f"/api/v1/projects/{pid}", headers=h)).json()
    assert project["current_version_id"] == v1
    assert len((await client.get(f"/api/v1/projects/{pid}/versions", headers=h)).json()) == 1


async def test_checksum_mismatch_fails_job(client: AsyncClient, settings: Settings) -> None:
    h, pid = await _setup(client)
    await _submit(client, h, pid, desk_batch())
    out = await FakeWorker(client, settings.data_root).run(corrupt=True)
    assert out["status"] == "failed" and out["error_code"] == "artifact_mismatch"
    project = (await client.get(f"/api/v1/projects/{pid}", headers=h)).json()
    assert project["current_version_id"] is None
    assert not list(Path(settings.data_root).glob("projects/*/*/versions/*"))
    assert not list(Path(settings.data_root).glob("projects/*/*/staging/*"))


async def test_duplicate_start_and_complete_are_idempotent(client: AsyncClient, settings: Settings) -> None:
    h, pid = await _setup(client)
    worker = FakeWorker(client, settings.data_root)
    await _submit(client, h, pid, desk_batch())
    spec = await worker.next_spec()
    await worker.run(spec)
    again = await client.post(f"/internal/v1/jobs/{spec.job_id}/start", json={"worker_id": "w2"}, headers=INTERNAL)
    assert again.json()["proceed"] is False
    dup = await client.post(
        f"/internal/v1/jobs/{spec.job_id}/complete",
        json={"job_id": str(spec.job_id), "success": False},
        headers=INTERNAL,
    )
    assert dup.status_code == 200 and dup.json()["status"] == "succeeded"


async def test_cancel_queued_and_running(client: AsyncClient, settings: Settings) -> None:
    h, pid = await _setup(client)
    worker = FakeWorker(client, settings.data_root)
    sub = await _submit(client, h, pid, desk_batch())
    res = await client.post(f"/api/v1/jobs/{sub['job_id']}/cancel", headers=h)
    assert res.json()["status"] == "cancelled"
    spec = await worker.next_spec()
    start = await client.post(f"/internal/v1/jobs/{spec.job_id}/start", json={"worker_id": "w"}, headers=INTERNAL)
    assert start.json()["proceed"] is False  # worker skips cancelled jobs
    assert (await client.post(f"/api/v1/jobs/{sub['job_id']}/cancel", headers=h)).status_code == 409

    sub = await _submit(client, h, pid, desk_batch())  # lock was released by the cancel
    spec = await worker.next_spec()
    await client.post(f"/internal/v1/jobs/{spec.job_id}/start", json={"worker_id": "w"}, headers=INTERNAL)
    res = await client.post(f"/api/v1/jobs/{sub['job_id']}/cancel", headers=h)
    assert res.json()["progress"]["stage"] == "cancelling"
    redis = client.app.state.redis  # type: ignore[attr-defined]
    assert await redis.get(cancel_flag_key(spec.job_id)) == b"1"
    out = await client.post(
        f"/internal/v1/jobs/{spec.job_id}/complete",
        headers=INTERNAL,
        json={"job_id": str(spec.job_id), "success": False, "error_code": "cancelled", "error_message": "Cancelled"},
    )
    assert out.json()["status"] == "cancelled"
    assert await redis.get(cancel_flag_key(spec.job_id)) is None


async def test_internal_endpoints_require_token(client: AsyncClient) -> None:
    jid = uuid.uuid4()
    for headers in ({}, {"X-Internal-Token": "wrong"}):
        res = await client.post(f"/internal/v1/jobs/{jid}/start", json={"worker_id": "w"}, headers=headers)
        assert res.status_code == 401


async def test_ownership_on_jobs_versions_assets(client: AsyncClient, settings: Settings) -> None:
    h, pid = await _setup(client)
    sub = await _submit(client, h, pid, desk_batch())
    v1 = (await FakeWorker(client, settings.data_root).run())["version_id"]
    other = auth((await register(client, email="b@example.com"))["access_token"])
    urls = [
        f"/api/v1/jobs/{sub['job_id']}",
        f"/api/v1/jobs/{sub['job_id']}/events",
        f"/api/v1/projects/{pid}/versions",
        f"/api/v1/projects/{pid}/versions/{v1}",
        f"/api/v1/projects/{pid}/versions/{v1}/assets/glb",
        f"/api/v1/projects/{pid}/thumbnail",
    ]
    for url in urls:
        assert (await client.get(url, headers=other)).status_code == 404, url
    assert (
        await client.post(f"/api/v1/projects/{pid}/modeling/execute", json=desk_batch(), headers=other)
    ).status_code == 404
    assert (await client.get(f"/api/v1/projects/{pid}/versions/{v1}/assets/secrets", headers=h)).status_code == 422


async def test_sse_replays_events_and_stops_at_terminal(client: AsyncClient, settings: Settings) -> None:
    h, pid = await _setup(client)
    sub = await _submit(client, h, pid, desk_batch())
    await FakeWorker(client, settings.data_root).run()
    res = await client.get(f"/api/v1/jobs/{sub['job_id']}/events", headers=h)
    assert res.status_code == 200 and res.headers["content-type"].startswith("text/event-stream")
    events = [line.split(": ", 1)[1] for line in res.text.splitlines() if line.startswith("event: ")]
    assert events[0] == "plan.validated" and "job.started" in events and "version.created" in events
    assert events[-1] == "job.completed"

    ids = [line.split(": ", 1)[1] for line in res.text.splitlines() if line.startswith("id: ")]
    resumed = await client.get(f"/api/v1/jobs/{sub['job_id']}/events", headers={**h, "Last-Event-ID": ids[-2]})
    assert [ln for ln in resumed.text.splitlines() if ln.startswith("event: ")] == ["event: job.completed"]


async def test_reconciler_quarantines_orphans(client: AsyncClient, settings: Settings, db) -> None:
    from app.services.storage import ProjectStorage
    from app.services.version_service import reconcile_storage

    h, pid = await _setup(client)
    await _submit(client, h, pid, desk_batch())
    await FakeWorker(client, settings.data_root).run()
    project_dir = next(Path(settings.data_root).glob(f"projects/*/{pid}"))
    orphan = project_dir / "versions" / "v0007"
    orphan.mkdir()
    (orphan / "scene.blend").write_bytes(b"x")
    stale = project_dir / "staging" / str(uuid.uuid4())
    stale.mkdir(parents=True)

    async with db.sessionmaker() as session:
        stats = await reconcile_storage(session, ProjectStorage(settings.data_root), orphan_minutes=-1)
    assert stats == {"quarantined_versions": 1, "removed_staging": 1}
    assert (project_dir / "versions" / "v0001").is_dir() and not orphan.exists()
    assert list((project_dir / "quarantine").glob("v0007-*"))
