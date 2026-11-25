"""Conversation API + the agent worker's internal contract, driven in-test (no LLM, no Blender)."""

from __future__ import annotations

import uuid
from pathlib import Path

import pytest
from httpx import AsyncClient

from app.core.config import Settings
from forma3d_shared.examples import black_metal_legs_batch, desk_batch
from forma3d_shared.schemas.agent import AGENT_JOB_STREAM, AgentJobSpec
from tests.test_modeling_pipeline import INTERNAL, FakeWorker, _setup, pipeline_settings


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return pipeline_settings(tmp_path)


async def _post(client: AsyncClient, h: dict, pid: str, text: str, **extra) -> dict:
    res = await client.post(f"/api/v1/projects/{pid}/conversation/messages", json={"content": text, **extra}, headers=h)
    assert res.status_code == 202, res.text
    return res.json()


async def _agent_spec(client: AsyncClient) -> AgentJobSpec:
    redis = client.app.state.redis  # type: ignore[attr-defined]
    entries = await redis.xrange(AGENT_JOB_STREAM)
    assert entries, "no agent job queued"
    entry_id, fields = entries[-1]
    await redis.xdel(AGENT_JOB_STREAM, entry_id)
    return AgentJobSpec.model_validate_json(fields[b"job"])


async def _start(client: AsyncClient, job_id) -> None:
    res = await client.post(f"/internal/v1/jobs/{job_id}/start", json={"worker_id": "agent-1"}, headers=INTERNAL)
    assert res.status_code == 200 and res.json()["proceed"], res.text


async def _complete(client: AsyncClient, job_id, **body) -> None:
    res = await client.post(f"/internal/v1/agent-jobs/{job_id}/complete", json=body, headers=INTERNAL)
    assert res.status_code == 204, res.text


async def _modeling_turn(client: AsyncClient, worker: FakeWorker, h: dict, pid: str, text: str, ops: list) -> dict:
    """One full agent turn: message -> agent job -> plan -> Blender job -> agent completes."""
    await _post(client, h, pid, text)
    spec = await _agent_spec(client)
    await _start(client, spec.job_id)
    ctx = (await client.get(f"/internal/v1/agent-jobs/{spec.job_id}/context", headers=INTERNAL)).json()
    assert ctx["user_message"] == text
    res = await client.post(
        f"/internal/v1/agent-jobs/{spec.job_id}/plan", json={"summary": text, "operations": ops}, headers=INTERNAL
    )
    assert res.status_code == 200, res.text
    mjob = res.json()["modeling_job_id"]
    await worker.run()
    job = (await client.get(f"/internal/v1/jobs/{mjob}", headers=INTERNAL)).json()
    assert job["status"] == "succeeded"
    await client.post(
        f"/internal/v1/agent-jobs/{spec.job_id}/runs",
        headers=INTERNAL,
        json={"agent_name": "planner", "model_name": "fake", "status": "succeeded", "duration_ms": 3},
    )
    await _complete(client, spec.job_id, success=True, intent="modeling", reply="Done.", modeling_job_id=mjob)
    return ctx


async def test_get_or_create_conversation_is_stable(client: AsyncClient, settings: Settings) -> None:
    h, pid = await _setup(client)
    a = (await client.get(f"/api/v1/projects/{pid}/conversation", headers=h)).json()
    b = (await client.get(f"/api/v1/projects/{pid}/conversation", headers=h)).json()
    assert a["id"] == b["id"] and a["messages"] == []


async def test_post_enqueues_agent_job_and_blocks_concurrent_turns(client: AsyncClient, settings: Settings) -> None:
    h, pid = await _setup(client)
    out = await _post(client, h, pid, "  Create a desk  ", selected_object_id="desk_top")
    assert out["message"]["role"] == "user" and out["message"]["content"] == "Create a desk"
    assert out["status"] == "queued"
    spec = await _agent_spec(client)
    assert str(spec.job_id) == out["job_id"] and str(spec.project_id) == pid
    res = await client.post(f"/api/v1/projects/{pid}/conversation/messages", json={"content": "again"}, headers=h)
    assert res.status_code == 409 and res.json()["code"] == "project_busy"
    events = await client.app.state.redis.xrange(f"events:{out['job_id']}")  # type: ignore[attr-defined]
    assert events  # job.queued published


async def test_rejects_empty_and_bad_selection(client: AsyncClient, settings: Settings) -> None:
    h, pid = await _setup(client)
    for body in ({"content": "   "}, {"content": "x", "selected_object_id": "../etc"}, {"content": "x", "y": 1}):
        res = await client.post(f"/api/v1/projects/{pid}/conversation/messages", json=body, headers=h)
        assert res.status_code == 422, body


async def test_ownership(client: AsyncClient, settings: Settings) -> None:
    _h, pid = await _setup(client)
    hb, _ = await _setup(client, email="b@example.com")
    assert (await client.get(f"/api/v1/projects/{pid}/conversation", headers=hb)).status_code == 404
    res = await client.post(f"/api/v1/projects/{pid}/conversation/messages", json={"content": "x"}, headers=hb)
    assert res.status_code == 404
    assert (await client.get(f"/api/v1/projects/{uuid.uuid4()}/conversation", headers=hb)).status_code == 404


async def test_internal_agent_endpoints_require_token(client: AsyncClient, settings: Settings) -> None:
    jid = uuid.uuid4()
    assert (await client.get(f"/internal/v1/agent-jobs/{jid}/context")).status_code in (401, 403)
    res = await client.get(f"/internal/v1/agent-jobs/{jid}/context", headers=INTERNAL)
    assert res.status_code == 404


async def test_invalid_plan_is_rejected_with_issues(client: AsyncClient, settings: Settings) -> None:
    h, pid = await _setup(client)
    await _post(client, h, pid, "paint the ghost")
    spec = await _agent_spec(client)
    await _start(client, spec.job_id)
    bad = [
        {
            "operation_id": "a",
            "operation_type": "assign_material",
            "target_object_id": "ghost",
            "parameters": {"material_id": "oak"},
        }
    ]
    res = await client.post(
        f"/internal/v1/agent-jobs/{spec.job_id}/plan", json={"summary": "x", "operations": bad}, headers=INTERNAL
    )
    assert res.status_code == 422 and res.json()["code"] == "invalid_batch" and res.json()["issues"]
    await _complete(client, spec.job_id, success=False, reply="Could not plan that.", error_code="plan_invalid")
    conv = (await client.get(f"/api/v1/projects/{pid}/conversation", headers=h)).json()
    assert [m["role"] for m in conv["messages"]] == ["user", "assistant"]
    assert conv["messages"][1]["metadata"]["error_code"] == "plan_invalid"
    # Project is free again.
    await _post(client, h, pid, "next")


async def test_question_and_export_turns(client: AsyncClient, settings: Settings) -> None:
    h, pid = await _setup(client)
    await _post(client, h, pid, "export it")
    spec = await _agent_spec(client)
    await _start(client, spec.job_id)
    await _complete(client, spec.job_id, success=True, intent="export", reply="Here is your GLB.")
    await _complete(client, spec.job_id, success=True, intent="export", reply="dup")  # idempotent
    conv = (await client.get(f"/api/v1/projects/{pid}/conversation", headers=h)).json()
    assert len(conv["messages"]) == 2
    last = conv["messages"][-1]
    assert last["intent"] == "export" and last["metadata"]["action"] == "export"
    job = (await client.get(f"/api/v1/jobs/{spec.job_id}", headers=h)).json()
    assert job["status"] == "succeeded"


async def test_vertical_slice_desk_scenario(client: AsyncClient, settings: Settings) -> None:
    h, pid = await _setup(client)
    worker = FakeWorker(client, settings.data_root)
    await _modeling_turn(client, worker, h, pid, "Create a modern wooden desk", desk_batch()["operations"])
    await _post(client, h, pid, "export it")
    spec = await _agent_spec(client)
    await _start(client, spec.job_id)
    await _complete(client, spec.job_id, success=True, intent="export", reply="Here you go.")
    exported = (await client.get(f"/api/v1/projects/{pid}/conversation", headers=h)).json()["messages"][-1]
    assert exported["metadata"]["export_version_number"] == 1 and exported["metadata"]["export_version_id"]
    ctx = await _modeling_turn(
        client, worker, h, pid, "Make the legs black metal", black_metal_legs_batch()["operations"]
    )
    # The second turn saw the first version's inventory and the prior conversation.
    assert ctx["version_number"] == 1 and ctx["inventory"]["objects"]
    assert [t["role"] for t in ctx["history"]] == ["user", "assistant"] * 2

    conv = (await client.get(f"/api/v1/projects/{pid}/conversation", headers=h)).json()
    assistants = [m for m in conv["messages"] if m["role"] == "assistant"]
    assert [m["metadata"].get("version_number") for m in assistants] == [1, None, 2]
    versions = (await client.get(f"/api/v1/projects/{pid}/versions", headers=h)).json()
    assert len(versions) == 2
