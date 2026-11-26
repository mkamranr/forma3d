"""Agent trace ("How this was made"): steps are upserted live by the worker, owners see summaries, admins
also see LLM prompts, other users see nothing; admins list all turns; prompt capture can be disabled/purged."""

from __future__ import annotations

import json
import uuid
from datetime import timedelta
from pathlib import Path

import pytest
from httpx import AsyncClient

from app.core.config import Settings
from app.core.time import utcnow
from app.services.maintenance import Maintenance
from app.services.storage import ProjectStorage
from forma3d_shared.schemas.agent import AGENT_GRAPH_KEY
from tests.conftest import auth, register
from tests.test_conversations import _agent_spec, _complete, _post, _start
from tests.test_modeling_pipeline import INTERNAL, _setup, pipeline_settings

PROMPT = {"messages": [{"role": "system", "content": "You are the planner", "images": 0}], "response": '{"parts":[]}'}


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return pipeline_settings(tmp_path)


async def run(client: AsyncClient, job_id, **body) -> None:
    res = await client.post(f"/internal/v1/agent-jobs/{job_id}/runs", json=body, headers=INTERNAL)
    assert res.status_code == 204, res.text


async def _turn(client: AsyncClient) -> tuple[dict, str, str]:
    """Admin owner (first account) with one traced turn: conversation -> planning(running -> ok) -> respond."""
    h, pid = await _setup(client)
    await _post(client, h, pid, "Create a desk")
    spec = await _agent_spec(client)
    jid = str(spec.job_id)
    await _start(client, jid)
    plan_step = str(uuid.uuid4())
    await run(
        client,
        jid,
        id=str(uuid.uuid4()),
        kind="node",
        node="conversation",
        seq=1,
        agent_name="conversation",
        status="succeeded",
        duration_ms=40,
        output_metadata={"intent": "create"},
    )
    await run(client, jid, id=plan_step, kind="node", node="planning", seq=2, agent_name="planning", status="running")
    await run(
        client,
        jid,
        kind="llm",
        node="planning",
        seq=2,
        agent_name="planning",
        model_name="agents-a1",
        status="succeeded",
        duration_ms=900,
        input_metadata={"attempt": 1, "prompt_tokens": 1200},
        output_metadata={"completion_tokens": 300},
        debug=PROMPT,
    )
    return h, pid, jid


async def test_trace_is_live_then_complete(client: AsyncClient) -> None:
    h, pid, jid = await _turn(client)
    await client.app.state.redis.set(  # type: ignore[attr-defined]
        AGENT_GRAPH_KEY, json.dumps({"nodes": [{"id": "planning"}], "edges": []})
    )
    url = f"/api/v1/projects/{pid}/agent-jobs/{jid}/trace"
    t = (await client.get(url, headers=h)).json()
    assert t["job"]["status"] == "running" and t["message"]["content"] == "Create a desk"
    assert [(s["node"], s["status"]) for s in t["steps"]] == [("conversation", "succeeded"), ("planning", "running")]
    assert t["graph"]["nodes"] == [{"id": "planning"}]
    call = t["steps"][1]["calls"][0]
    assert call["kind"] == "llm" and call["model"] == "agents-a1"
    assert t["totals"]["llm_calls"] == 1 and t["totals"]["prompt_tokens"] == 1200

    # the step ends: same id -> updated in place, not duplicated
    step_id = t["steps"][1]["id"]
    await run(
        client,
        jid,
        id=step_id,
        kind="node",
        node="planning",
        seq=2,
        agent_name="planning",
        status="succeeded",
        duration_ms=950,
        output_metadata={"parts": ["desk_top"]},
    )
    await _complete(client, jid, success=True, intent="question", reply="Done.")
    t = (await client.get(url, headers=h)).json()
    assert [s["status"] for s in t["steps"]] == ["succeeded", "succeeded"]
    assert t["steps"][1]["output"] == {"parts": ["desk_top"]} and t["job"]["status"] == "succeeded"


async def test_prompts_are_admin_only_and_traces_owner_only(client: AsyncClient) -> None:
    h_admin, pid, jid = await _turn(client)  # first account = admin, owns this project
    t = (await client.get(f"/api/v1/projects/{pid}/agent-jobs/{jid}/trace", headers=h_admin)).json()
    assert t["steps"][1]["calls"][0]["debug"] == PROMPT and t["include_debug"] is True

    # a regular user with their own traced turn sees summaries but no prompts
    tokens = await register(client, email="bob@example.com", name="Bob")
    hb = auth(tokens["access_token"])
    pb = (await client.post("/api/v1/projects", json={"name": "Lamp"}, headers=hb)).json()["id"]
    await _post(client, hb, pb, "Make a lamp")
    spec = await _agent_spec(client)
    await _start(client, spec.job_id)
    await run(
        client,
        spec.job_id,
        kind="llm",
        node="planning",
        seq=1,
        agent_name="planning",
        model_name="m",
        status="succeeded",
        debug=PROMPT,
    )
    tb = (await client.get(f"/api/v1/projects/{pb}/agent-jobs/{spec.job_id}/trace", headers=hb)).json()
    assert "debug" not in tb["calls"][0] and tb["include_debug"] is False
    assert "You are the planner" not in json.dumps(tb)

    # Bob cannot read Ada's trace (also not through his own project id)
    for url in (f"/api/v1/projects/{pid}/agent-jobs/{jid}/trace", f"/api/v1/projects/{pb}/agent-jobs/{jid}/trace"):
        assert (await client.get(url, headers=hb)).status_code == 404
    assert (await client.get("/api/v1/admin/agent-jobs", headers=hb)).status_code == 403

    # the admin lists everyone's turns and opens any trace with prompts
    listing = (await client.get("/api/v1/admin/agent-jobs", headers=h_admin)).json()
    assert listing["total"] == 2
    lamp = next(i for i in listing["items"] if i["user_email"] == "bob@example.com")
    assert lamp["message"] == "Make a lamp" and lamp["project_name"] == "Lamp" and lamp["llm_calls"] == 1
    assert (await client.get("/api/v1/admin/agent-jobs?q=lamp", headers=h_admin)).json()["total"] == 1
    t = (await client.get(f"/api/v1/admin/agent-jobs/{spec.job_id}/trace", headers=h_admin)).json()
    assert t["calls"][0]["debug"] == PROMPT


async def test_prompt_capture_can_be_disabled_and_is_purged(client: AsyncClient, settings: Settings) -> None:
    h, pid, jid = await _turn(client)
    url = f"/api/v1/projects/{pid}/agent-jobs/{jid}/trace"
    await client.put("/api/v1/admin/config", json={"values": {"agent_trace_prompts": False}}, headers=h)
    await run(
        client,
        jid,
        kind="llm",
        node="planning",
        seq=2,
        agent_name="planning",
        model_name="m",
        status="succeeded",
        debug=PROMPT,
    )
    calls = (await client.get(url, headers=h)).json()["steps"][1]["calls"]
    assert [c["debug"] is not None for c in calls] == [True, False]

    app = client.app  # type: ignore[attr-defined]
    m = Maintenance(app.state.db.sessionmaker, app.state.redis, settings, ProjectStorage(settings.data_root))
    assert await m.purge_trace_prompts(utcnow()) == 0  # still fresh
    assert await m.purge_trace_prompts(utcnow() + timedelta(days=settings.agent_trace_prompt_retention_days + 1)) == 1
    calls = (await client.get(url, headers=h)).json()["steps"][1]["calls"]
    assert all(c["debug"] is None for c in calls) and calls[0]["input"]["prompt_tokens"] == 1200  # summary kept


async def test_unfinished_steps_of_an_ended_turn_show_as_interrupted(client: AsyncClient) -> None:
    h, pid, jid = await _turn(client)
    await _complete(client, jid, success=False, reply="Something went wrong.", error_code="agent_error")
    t = (await client.get(f"/api/v1/projects/{pid}/agent-jobs/{jid}/trace", headers=h)).json()
    assert t["steps"][1]["status"] == "interrupted"
