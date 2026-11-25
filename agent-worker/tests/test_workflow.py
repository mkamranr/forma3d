"""End-to-end LangGraph workflow tests against the simulated backend + Blender (tests/sim.py)."""

from __future__ import annotations

import json
import uuid

import fakeredis
import pytest
from forma3d_shared.schemas.agent import AgentJobSpec
from forma3d_shared.schemas.events import cancel_flag_key, event_stream_key
from langgraph.checkpoint.memory import InMemorySaver

from app.backend_client import BackendUnavailable
from app.config import AgentConfig
from app.providers.fake import FakeProvider, ScriptedProvider
from app.worker import Worker

from .sim import PID, FlakyBackend, SimBackend

CONV = uuid.UUID("00000000-0000-4000-8000-000000000003")


def make_worker(backend, provider=None, checkpointer=None, r=None, **cfg):  # noqa: ANN001
    provider = provider or FakeProvider()
    providers = (
        provider if isinstance(provider, dict) else {a: provider for a in ("conversation", "planning", "revision")}
    )
    return Worker(
        AgentConfig(worker_id="w1", **cfg),
        r=r or backend.redis,
        backend=backend,
        providers=providers,
        checkpointer=checkpointer if checkpointer is not None else InMemorySaver(),
        sleep=lambda _s: None,
    )


def say(worker, backend, text, selected=None, *, reclaim=False):  # noqa: ANN001
    jid = backend.post(text, selected)
    worker.runner.run(
        AgentJobSpec(job_id=jid, project_id=PID, conversation_id=CONV, message_id=uuid.uuid4()), reclaim=reclaim
    )
    return jid, backend.completed.get(jid)


def events(r, jid):  # noqa: ANN001
    return [json.loads(f["event"]) for _id, f in r.xrange(event_stream_key(jid))]


def agents_run(backend, jid):  # noqa: ANN001
    return [rec.agent_name for j, rec in backend.runs if j == jid]


@pytest.fixture
def r():
    return fakeredis.FakeRedis(decode_responses=True)


@pytest.fixture
def sim(r):  # noqa: ANN001
    return SimBackend(r)


# ---------------------------------------------------------------------------- §23 scenario
def test_spec_scenario_end_to_end(sim, r):  # noqa: ANN001
    w = make_worker(sim)
    jid, done = say(w, sim, "Create a modern wooden desk")
    assert done.success and done.intent == "modeling", done
    assert done.metadata["quality"]["passed"] is True and done.metadata["quality"]["revisions"] == 0
    objs = sim.objects()
    assert set(objs) == {"desk_top", "leg_fl", "leg_fr", "leg_bl", "leg_br"}
    # agents collaborated, inspection happened before planning and modeling
    order = agents_run(sim, jid)
    assert (
        order.index("inspection")
        < order.index("conversation")
        < order.index("planning")
        < order.index("modeling")
        < order.index("quality")
    )
    types = [e["type"] for e in events(r, jid)]
    assert {
        "agent.started",
        "plan.created",
        "plan.validated",
        "blender.started",
        "quality.completed",
        "version.created",
    } <= set(types)
    v1 = sim.objects()

    _, done = say(w, sim, "Make the legs black metal")
    assert done.success and done.metadata["quality"]["passed"]
    objs = sim.objects()
    assert all(objs[i]["material_ids"] == ["black_metal"] for i in ("leg_fl", "leg_fr", "leg_bl", "leg_br"))
    assert objs["desk_top"] == v1["desk_top"]  # untouched

    _, done = say(w, sim, "Add two drawers")
    assert done.success and done.metadata["quality"]["passed"], done
    objs = sim.objects()
    assert {"drawer_left", "drawer_right", "drawer_left_handle", "drawer_right_handle"} <= set(objs)
    top_bottom = objs["desk_top"]["location"][2] - objs["desk_top"]["dimensions"][2] / 2
    drawer_top = objs["drawer_left"]["location"][2] + objs["drawer_left"]["dimensions"][2] / 2
    assert abs(top_bottom - drawer_top) < 0.005  # flush under the top
    v3 = sim.objects()

    _, done = say(w, sim, "Change the top to walnut")
    assert done.success
    objs = sim.objects()
    assert objs["desk_top"]["material_ids"] == ["walnut"]
    assert {k: v for k, v in objs.items() if k != "desk_top"} == {k: v for k, v in v3.items() if k != "desk_top"}

    _, done = say(w, sim, "Export it as GLB")
    assert done.success and done.intent == "export"
    assert len(sim.versions) == 4 and sim.submissions == 4


# ---------------------------------------------------------------------------- Scenario C: clarification
def _desk_with_drawers(w, sim):  # noqa: ANN001
    say(w, sim, "Create a modern wooden desk")
    say(w, sim, "Add two drawers")
    assert len(sim.versions) == 2


def test_ambiguous_request_asks_then_resumes(sim, r):  # noqa: ANN001
    w = make_worker(sim)
    _desk_with_drawers(w, sim)
    before = sim.objects()
    jid, done = say(w, sim, "Make the drawer smaller")
    assert done.success and done.intent == "clarify"
    assert "left one" in done.reply and len(sim.versions) == 2  # nothing built
    cfg = {"configurable": {"thread_id": sim.thread}}
    assert w.runner.graph.get_state(cfg).next == ("wait_answer",)

    jid2, done2 = say(w, sim, "The left one")
    assert done2.success and done2.intent == "modeling", done2
    after = sim.objects()
    assert after["drawer_left"]["dimensions"][0] == pytest.approx(before["drawer_left"]["dimensions"][0] * 0.8)
    assert after["drawer_right"] == before["drawer_right"]
    assert len(sim.versions) == 3
    assert not w.runner.graph.get_state(cfg).next  # the thread finished


def test_selected_object_resolves_ambiguity(sim):  # noqa: ANN001
    w = make_worker(sim)
    _desk_with_drawers(w, sim)
    _, done = say(w, sim, "Make the drawer smaller", selected="drawer_right")
    assert done.intent == "modeling"
    assert sim.objects()["drawer_right"]["dimensions"][0] == pytest.approx(0.4)


# ---------------------------------------------------------------------------- QA -> revision
def test_failed_qa_triggers_revision(sim):  # noqa: ANN001
    w = make_worker(sim, FakeProvider(flaws={"drawer_gap"}))
    say(w, sim, "Create a modern wooden desk")
    jid, done = say(w, sim, "Add two drawers")
    assert done.success, done
    q = done.metadata["quality"]
    assert q["passed"] and q["revisions"] == 1
    assert sorted(m["status"] for m in sim.mjobs.values()) == ["discarded", "succeeded", "succeeded"]
    assert len(sim.versions) == 2  # the flawed attempt never became a version
    objs = sim.objects()
    top_bottom = objs["desk_top"]["location"][2] - objs["desk_top"]["dimensions"][2] / 2
    assert abs(objs["drawer_left"]["location"][2] + 0.06 - top_bottom) < 0.005
    assert "revision" in agents_run(sim, jid)


def test_revision_is_bounded_and_rolls_back(r):  # noqa: ANN001
    sim = SimBackend(r, fail_blender=10)
    fake = FakeProvider()
    patch = json.dumps(
        {"summary": "smaller bevel", "reply": "ok", "parts": [{"id": "desk_top", "action": "modify", "bevel": 0.002}]}
    )
    reviser = ScriptedProvider([patch] * 10)
    w = make_worker(sim, {"conversation": fake, "planning": fake, "revision": reviser}, max_revisions=2)
    jid, done = say(w, sim, "Create a modern wooden desk")
    assert not done.success and done.error_code == "quality_failed"
    assert len(reviser.calls) == 2 and done.metadata["quality"]["revisions"] == 2
    assert "unchanged" in done.reply
    assert sim.submissions == 3  # first attempt + 2 revisions
    assert sim.versions == []


def test_revision_agent_that_changes_nothing_ends_in_failure(r):  # noqa: ANN001
    sim = SimBackend(r, fail_blender=10)
    fake = FakeProvider()
    providers = {"conversation": fake, "planning": fake, "revision": ScriptedProvider(["{}"] * 10)}
    w = make_worker(sim, providers)
    _, done = say(w, sim, "Create a modern wooden desk")
    assert not done.success and sim.submissions == 1 and sim.versions == []


# ---------------------------------------------------------------------------- invalid plans
def test_invalid_plan_is_rejected_before_blender(sim):  # noqa: ANN001
    bad = json.dumps(
        {
            "summary": "x",
            "reply": "x",
            "parts": [
                {
                    "id": "Top!!",
                    "action": "create",
                    "name": "T",
                    "shape": "box",
                    "size": [1, 1, -2],
                    "position": [0, 0, 0],
                }
            ],
        }
    )
    fake = FakeProvider()
    planner = ScriptedProvider([bad] * 5)
    w = make_worker(sim, {"conversation": fake, "planning": planner, "revision": fake}, max_retries=2)
    jid, done = say(w, sim, "Create a modern wooden desk")
    assert not done.success and done.error_code == "plan_invalid"
    assert len(planner.calls) == 3  # 1 + 2 repairs, each told what was wrong
    assert "REJECTED" in planner.calls[1][-1].content
    assert sim.submissions == 0 and sim.versions == []


def test_planner_repairs_after_feedback(sim):  # noqa: ANN001
    from app.providers.fake import desk_plan

    bad = desk_plan()
    bad["parts"][1]["parent"] = "no_such_part"
    planner = ScriptedProvider([json.dumps(bad), json.dumps(desk_plan())])
    fake = FakeProvider()
    w = make_worker(sim, {"conversation": fake, "planning": planner, "revision": fake})
    _, done = say(w, sim, "Create a modern wooden desk")
    assert done.success and len(planner.calls) == 2 and len(sim.versions) == 1


# ---------------------------------------------------------------------------- versions & questions
def test_undo_restore_and_question(sim):  # noqa: ANN001
    w = make_worker(sim)
    _desk_with_drawers(w, sim)
    _, done = say(w, sim, "How many objects are there?")
    assert done.intent == "question" and "9 objects" in done.reply
    _, done = say(w, sim, "Undo")
    assert done.success and done.metadata["restored"] == 1 and "drawer_left" not in sim.objects()
    _, done = say(w, sim, "Go back to version 2")
    assert done.success and "drawer_left" in sim.objects()
    assert sim.submissions == 2


def test_undo_with_nothing_to_undo(sim):  # noqa: ANN001
    w = make_worker(sim)
    say(w, sim, "Create a modern wooden desk")
    _, done = say(w, sim, "undo")
    assert not done.success and done.error_code == "nothing_to_undo"


# ---------------------------------------------------------------------------- cancel
def test_cancel_before_build(sim, r):  # noqa: ANN001
    w = make_worker(sim)
    jid = sim.post("Create a modern wooden desk")
    r.set(cancel_flag_key(jid), "1")
    w.runner.run(AgentJobSpec(job_id=jid, project_id=PID, conversation_id=CONV, message_id=uuid.uuid4()))
    assert sim.completed[jid].error_code == "cancelled" and sim.submissions == 0


# ---------------------------------------------------------------------------- crash & resume
@pytest.mark.parametrize("crash_on", ["commit_staged", "staged_inventory", "complete"])
def test_resume_after_crash_from_persisted_checkpoint(r, tmp_path, crash_on):  # noqa: ANN001
    from app.checkpoint import make_checkpointer

    url = f"sqlite:///{(tmp_path / 'ckpt.db').as_posix()}"
    sim = FlakyBackend(r, crash_on="")
    w1 = make_worker(sim, checkpointer=make_checkpointer(url))
    say(w1, sim, "Create a modern wooden desk")

    sim.crash_on = crash_on
    jid = sim.post("Add two drawers")
    spec = AgentJobSpec(job_id=jid, project_id=PID, conversation_id=CONV, message_id=uuid.uuid4())
    with pytest.raises(BackendUnavailable):
        w1.runner.run(spec)  # worker "dies" mid-graph; the job stays running and un-ACKed
    assert jid not in sim.completed and sim.jobs[jid]["status"] == "running"

    # a brand-new worker process with a fresh checkpointer connection on the same database
    w2 = make_worker(sim, checkpointer=make_checkpointer(url))
    w2.runner.run(spec, reclaim=True)
    done = sim.completed[jid]
    assert done.success and done.metadata["quality"]["passed"], done
    assert sim.submissions == 2  # the Blender build was not repeated
    assert len(sim.versions) == 2 and "drawer_left" in sim.objects()


def test_worker_reclaims_idle_stream_entries(r, sim):  # noqa: ANN001
    from forma3d_shared.schemas.agent import AGENT_JOB_GROUP, AGENT_JOB_STREAM

    w = make_worker(sim)
    w.ensure_group()
    jid = sim.post("Create a modern wooden desk")
    spec = AgentJobSpec(job_id=jid, project_id=PID, conversation_id=CONV, message_id=uuid.uuid4())
    r.xadd(AGENT_JOB_STREAM, {"job": spec.model_dump_json()})
    r.xreadgroup(AGENT_JOB_GROUP, "dead-worker", {AGENT_JOB_STREAM: ">"}, count=1)  # delivered, never ACKed
    sim.jobs[jid]["status"] = "running"
    assert w.reclaim_pending(idle_ms=0) == 1
    assert sim.completed[jid].success and r.xpending(AGENT_JOB_STREAM, AGENT_JOB_GROUP)["pending"] == 0
