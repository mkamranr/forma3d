"""Phase 5 backend contract: staged Blender results (QA before commit), agent-turn exclusivity,
idempotent plan attempts, agent restore/undo and reclaiming a running agent job."""

from __future__ import annotations

from pathlib import Path

import pytest
from httpx import AsyncClient

from app.core.config import Settings
from forma3d_shared.examples import black_metal_legs_batch, desk_batch
from tests.test_conversations import _agent_spec, _complete, _post, _start
from tests.test_modeling_pipeline import INTERNAL, FakeWorker, _setup, pipeline_settings


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return pipeline_settings(tmp_path)


async def _turn(client: AsyncClient, h: dict, pid: str, text: str = "Create a desk"):
    await _post(client, h, pid, text)
    spec = await _agent_spec(client)
    await _start(client, spec.job_id)
    return spec


async def _submit(client: AsyncClient, job_id, ops, attempt=0, defer=True) -> dict:
    res = await client.post(
        f"/internal/v1/agent-jobs/{job_id}/plan",
        json={"summary": "s", "operations": ops, "attempt": attempt, "defer_commit": defer},
        headers=INTERNAL,
    )
    assert res.status_code == 200, res.text
    return res.json()


async def _versions(client: AsyncClient, h: dict, pid: str) -> list[int]:
    return [v["version_number"] for v in (await client.get(f"/api/v1/projects/{pid}/versions", headers=h)).json()]


async def test_staged_result_is_reviewed_then_committed(client: AsyncClient, settings: Settings) -> None:
    h, pid = await _setup(client)
    worker = FakeWorker(client, settings.data_root)
    spec = await _turn(client, h, pid)
    mjob = (await _submit(client, spec.job_id, desk_batch()["operations"]))["modeling_job_id"]
    await worker.run()

    job = (await client.get(f"/internal/v1/jobs/{mjob}", headers=INTERNAL)).json()
    assert job["status"] == "staged"
    assert await _versions(client, h, pid) == []  # nothing committed before QA
    inv = (await client.get(f"/internal/v1/jobs/{mjob}/staged", headers=INTERNAL)).json()
    assert {o["object_id"] for o in inv["objects"]} >= {"desk_top"}

    # The lock is still held: a second staged attempt cannot start until this one is resolved.
    res = await client.post(
        f"/internal/v1/agent-jobs/{spec.job_id}/plan",
        json={"summary": "s", "operations": desk_batch()["operations"], "attempt": 1, "defer_commit": True},
        headers=INTERNAL,
    )
    assert res.status_code == 409 and res.json()["code"] == "project_busy"

    out = (await client.post(f"/internal/v1/jobs/{mjob}/commit", headers=INTERNAL)).json()
    assert out["version_number"] == 1
    again = await client.post(f"/internal/v1/jobs/{mjob}/commit", headers=INTERNAL)  # idempotent
    assert again.status_code == 200 and again.json()["version_number"] == 1
    assert await _versions(client, h, pid) == [1]
    await _complete(
        client,
        spec.job_id,
        success=True,
        intent="modeling",
        reply="Done.",
        modeling_job_id=mjob,
        metadata={"quality": {"passed": True, "checks": 12, "revisions": 0}},
    )
    conv = (await client.get(f"/api/v1/projects/{pid}/conversation", headers=h)).json()
    meta = conv["messages"][-1]["metadata"]
    assert meta["version_number"] == 1 and meta["quality"]["passed"] is True


async def test_discard_then_revised_attempt(client: AsyncClient, settings: Settings) -> None:
    h, pid = await _setup(client)
    worker = FakeWorker(client, settings.data_root)
    spec = await _turn(client, h, pid)
    first = (await _submit(client, spec.job_id, desk_batch()["operations"], attempt=0))["modeling_job_id"]
    await worker.run(commit_scene=False)
    res = await client.post(
        f"/internal/v1/jobs/{first}/discard", json={"reason": "QA: drawer floats"}, headers=INTERNAL
    )
    assert res.status_code == 200 and res.json()["status"] == "discarded"
    staging = list(Path(settings.data_root).rglob("staging/*"))
    assert staging == []  # staged artifacts removed

    second = (await _submit(client, spec.job_id, desk_batch()["operations"], attempt=1))["modeling_job_id"]
    assert second != first
    # Resubmitting the same attempt (worker resumed after a crash) returns the same job.
    assert (await _submit(client, spec.job_id, desk_batch()["operations"], attempt=1))["modeling_job_id"] == second
    await worker.run()
    out = (await client.post(f"/internal/v1/jobs/{second}/commit", headers=INTERNAL)).json()
    assert out["version_number"] == 1  # the discarded attempt never became a version


async def test_ui_edits_wait_for_the_agent_turn(client: AsyncClient, settings: Settings) -> None:
    h, pid = await _setup(client)
    await _post(client, h, pid, "Create a desk")
    res = await client.post(f"/api/v1/projects/{pid}/modeling/execute", json=desk_batch(), headers=h)
    assert res.status_code == 409 and res.json()["code"] == "project_busy"
    res = await client.post(f"/api/v1/projects/{pid}/scene/starter", headers=h)
    assert res.status_code == 409


async def test_agent_undo_and_restore(client: AsyncClient, settings: Settings) -> None:
    h, pid = await _setup(client)
    worker = FakeWorker(client, settings.data_root)
    for ops in (desk_batch()["operations"], black_metal_legs_batch()["operations"]):
        spec = await _turn(client, h, pid)
        mjob = (await _submit(client, spec.job_id, ops, defer=False))["modeling_job_id"]
        await worker.run()
        await _complete(client, spec.job_id, success=True, intent="modeling", reply="ok", modeling_job_id=mjob)

    spec = await _turn(client, h, pid, "undo that")
    ctx = (await client.get(f"/internal/v1/agent-jobs/{spec.job_id}/context", headers=INTERNAL)).json()
    assert ctx["version_numbers"] == [2, 1] and ctx["thread_id"] and ctx["head_version_id"]
    res = await client.post(f"/internal/v1/agent-jobs/{spec.job_id}/restore", json={"undo": True}, headers=INTERNAL)
    assert res.status_code == 200 and res.json()["version_number"] == 1
    await _complete(
        client, spec.job_id, success=True, intent="undo", reply="Back to version 1.", metadata={"restored": 1}
    )
    scene = (await client.get(f"/api/v1/projects/{pid}/scene", headers=h)).json()
    assert scene["version"]["version_number"] == 1

    spec = await _turn(client, h, pid, "restore version 2")
    res = await client.post(
        f"/internal/v1/agent-jobs/{spec.job_id}/restore", json={"version_number": 2}, headers=INTERNAL
    )
    assert res.json()["version_number"] == 2
    res = await client.post(
        f"/internal/v1/agent-jobs/{spec.job_id}/restore", json={"version_number": 9}, headers=INTERNAL
    )
    assert res.status_code == 404


async def test_reclaim_resumes_a_running_agent_job_only(client: AsyncClient, settings: Settings) -> None:
    h, pid = await _setup(client)
    spec = await _turn(client, h, pid)
    url = f"/internal/v1/jobs/{spec.job_id}/start"
    assert not (await client.post(url, json={"worker_id": "w2"}, headers=INTERNAL)).json()["proceed"]
    res = (await client.post(url, json={"worker_id": "w2", "reclaim": True}, headers=INTERNAL)).json()
    assert res["proceed"] and res["reason"] == "resumed"
    await _complete(client, spec.job_id, success=True, intent="question", reply="ok")
    res = (await client.post(url, json={"worker_id": "w3", "reclaim": True}, headers=INTERNAL)).json()
    assert not res["proceed"]  # finished jobs are never re-run
