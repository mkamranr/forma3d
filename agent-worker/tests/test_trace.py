"""Agent trace: every graph node is recorded live (running -> final status) in order, LLM calls are attached
to the node that made them (with prompt/response for admins), and the graph structure is published."""

from __future__ import annotations

import json

import fakeredis
import pytest

from app.graph.trace import GRAPH_KEY, summarize
from app.providers.fake import FakeProvider

from .sim import SimBackend
from .test_workflow import _desk_with_drawers, make_worker, say


@pytest.fixture
def r():
    return fakeredis.FakeRedis(decode_responses=True)


@pytest.fixture
def sim(r):  # noqa: ANN001
    return SimBackend(r)


def rows(sim, jid):  # noqa: ANN001
    return [rec for j, rec in sim.runs if j == jid]


def final_nodes(sim, jid):  # noqa: ANN001
    """Last record per node step (rows are updated in place by id), ordered by seq."""
    last = {}
    for rec in rows(sim, jid):
        if rec.kind == "node":
            last[rec.id] = rec
    return sorted(last.values(), key=lambda r: r.seq)


def test_modeling_turn_trace(sim, r):  # noqa: ANN001
    w = make_worker(sim)
    jid, done = say(w, sim, "Create a modern wooden desk")
    assert done.success
    nodes = final_nodes(sim, jid)
    path = [n.node for n in nodes]
    assert path == [
        "load_context",
        "inspect_scene",
        "vision",
        "conversation",
        "planning",
        "modeling",
        "execute",
        "quality",
        "commit",
        "respond",
    ]
    assert [n.seq for n in nodes] == list(range(1, len(nodes) + 1))
    status = {n.node: n.status for n in nodes}
    assert status["vision"] == "skipped" and status["quality"] == "succeeded" and status["respond"] == "succeeded"

    # every step was first recorded as running (live view), then updated with the same id
    started = [rec for rec in rows(sim, jid) if rec.kind == "node" and rec.status == "running"]
    assert {s.id for s in started} == {n.id for n in nodes}

    by = {n.node: n.output_metadata for n in nodes}
    assert by["conversation"]["intent"] in ("create", "modify", "modeling")
    assert "desk_top" in by["planning"]["parts"] or by["planning"]["parts"]
    assert by["modeling"]["operations"] > 0 and by["modeling"]["by_type"]
    assert by["quality"]["passed"] is True and by["quality"]["checks"] > 0
    assert by["commit"]["reply"] and by["respond"]["success"] is True

    # LLM calls belong to the node that made them and carry the prompt/response (admin-only detail)
    llm = [rec for rec in rows(sim, jid) if rec.kind == "llm"]
    assert {c.node for c in llm} >= {"conversation", "planning"}
    seq_of = {n.node: n.seq for n in nodes}
    assert all(c.seq == seq_of[c.node] for c in llm)
    plan_call = next(c for c in llm if c.node == "planning")
    assert plan_call.debug["messages"][0]["role"] == "system" and plan_call.debug["response"]
    # deterministic agents are attached too
    assert any(rec.kind == "agent" and rec.agent_name == "quality" and rec.node == "quality" for rec in rows(sim, jid))

    graph = json.loads(r.get(GRAPH_KEY))
    ids = {n["id"] for n in graph["nodes"]}
    assert {"__start__", "planning", "quality", "revise", "__end__"} <= ids
    assert any(e["source"] == "quality" and e["target"] == "revise" and e["conditional"] for e in graph["edges"])


def test_revision_loop_is_visible(sim):  # noqa: ANN001
    w = make_worker(sim, FakeProvider(flaws={"drawer_gap"}))
    say(w, sim, "Create a modern wooden desk")
    jid, done = say(w, sim, "Add two drawers")
    assert done.success
    path = [n.node for n in final_nodes(sim, jid)]
    i = path.index("revise")
    assert path[i - 1] == "quality" and path[i + 1 : i + 4] == ["modeling", "execute", "quality"]
    first_qa = next(n for n in final_nodes(sim, jid) if n.node == "quality")
    assert first_qa.status == "failed" and first_qa.output_metadata["issues"]


def test_clarification_waits_and_resumes(sim):  # noqa: ANN001
    w = make_worker(sim)
    _desk_with_drawers(w, sim)
    jid, done = say(w, sim, "Make the drawer smaller")
    assert done.intent == "clarify"
    nodes = final_nodes(sim, jid)
    assert [n.node for n in nodes][-2:] == ["ask", "wait_answer"]
    assert nodes[-1].status == "waiting" and nodes[-2].output_metadata["question"]

    jid2, _ = say(w, sim, "The left one")
    path2 = [n.node for n in final_nodes(sim, jid2)]
    assert path2[0] == "wait_answer" and path2[1] == "load_context" and path2[-1] == "respond"


def test_trace_failures_never_break_a_turn(sim):  # noqa: ANN001
    def broken(job, rec):  # noqa: ANN001
        raise RuntimeError("backend down")

    w = make_worker(sim)
    w.runner.workflow.d.backend.record_run = broken  # type: ignore[method-assign]
    _, done = say(w, sim, "Create a modern wooden desk")
    assert done.success


def test_summaries_are_short_and_safe() -> None:
    status, out = summarize("respond", {}, {"outcome": {"success": False, "reply": "x" * 1000, "error_code": "e"}})
    assert status == "succeeded" and len(out["reply"]) <= 300 and out["error_code"] == "e"
    status, out = summarize("planning", {"outcome": {"success": False, "error_message": "boom"}}, {})
    assert status == "failed" and out["error"] == "boom"
    assert summarize("vision", {}, {})[0] == "skipped"
