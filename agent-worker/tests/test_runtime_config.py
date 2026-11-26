"""Settings changed in the app's Configuration page: applied by the worker before jobs; connection test."""

from __future__ import annotations

import json
import threading

import fakeredis
import httpx
from forma3d_shared.runtime_config import CONFIG_VERSION_KEY, LLM_TEST_QUEUE, LLM_TEST_RESULT
from langgraph.checkpoint.memory import InMemorySaver

from app.config import AgentConfig
from app.providers.fake import FakeProvider
from app.providers.openai_compatible import OpenAICompatibleProvider
from app.runtime import apply_overrides, run_llm_test
from app.worker import Worker

from .sim import SimBackend


def test_overrides_layer_over_the_environment():
    base = AgentConfig(llm_model="env-model", agent_models={"planning": "env-planner"}, max_retries=2)
    cfg = apply_overrides(
        base,
        {
            "llm_model": "app-model",
            "llm_model_vision": "qwen-vl",
            "llm_model_planning": "",  # empty = back to the main model
            "agent_max_retries": 4,
            "llm_provider": "FAKE",
            "unknown_key": 1,
        },
    )
    assert cfg.llm_model == "app-model" and cfg.max_retries == 4 and cfg.llm_provider == "fake"
    assert cfg.model_for("vision") == "qwen-vl" and cfg.model_for("planning") == "app-model"
    assert base.llm_model == "env-model"  # the environment config is untouched


def test_worker_applies_changes_before_the_next_job():
    r = fakeredis.FakeRedis(decode_responses=True)
    sim = SimBackend(r)
    w = Worker(
        AgentConfig(worker_id="w1", llm_provider="vllm", llm_base_url="http://x/v1"),
        r=r,
        backend=sim,
        checkpointer=InMemorySaver(),
        sleep=lambda _s: None,
    )
    w.refresh_config()
    assert isinstance(w.runner.workflow.d.providers["planning"], OpenAICompatibleProvider)
    sim.config = {"llm_provider": "fake", "agent_max_revisions": 0, "modeling_max_scene_objects": 3}
    w.refresh_config()  # version unchanged: nothing re-read
    assert isinstance(w.runner.workflow.d.providers["planning"], OpenAICompatibleProvider)
    r.incr(CONFIG_VERSION_KEY)
    w.refresh_config()
    d = w.runner.workflow.d
    assert isinstance(d.providers["planning"], FakeProvider)
    assert d.max_revisions == 0 and d.limits.max_scene_objects == 3 and w.cfg.llm_provider == "fake"


def _llm_server(vision_answer: str = "Red", served: tuple[str, ...] = ("m",)):
    def handler(req: httpx.Request) -> httpx.Response:
        if req.url.path.endswith("/models"):
            return httpx.Response(200, json={"data": [{"id": s} for s in served]})
        body = json.loads(req.content)
        content = body["messages"][-1]["content"]
        if isinstance(content, list):
            text = vision_answer
        elif "JSON" in content:
            text = '{"ok": true}'
        else:
            text = "OK"
        return httpx.Response(200, json={"model": body["model"], "choices": [{"message": {"content": text}}]})

    return httpx.Client(base_url="http://llm/v1", transport=httpx.MockTransport(handler))


def _providers(cfg: AgentConfig, client: httpx.Client):
    p = OpenAICompatibleProvider("http://llm/v1", cfg.llm_model, client=client, retries=0)
    return {a: p for a in ("conversation", "planning", "revision", "vision")}


def test_connection_test_reports_each_check():
    cfg = AgentConfig(llm_base_url="http://llm/v1", llm_model="m")
    client = _llm_server()
    res = run_llm_test(cfg, _providers(cfg, client), http=client)
    assert res["ok"] is True and res["models"] == ["m"]
    assert [(c["name"], c["ok"]) for c in res["checks"]] == [
        ("server", True),
        ("chat", True),
        ("structured", True),
        ("vision", True),
    ]


def test_connection_test_explains_problems():
    cfg = AgentConfig(llm_base_url="http://llm/v1", llm_model="typo-model")
    client = _llm_server(vision_answer="blue", served=("real-model",))
    res = run_llm_test(cfg, _providers(cfg, client), http=client)
    assert res["ok"] is False
    server = res["checks"][0]
    assert not server["ok"] and "typo-model is not served" in server["detail"] and "real-model" in server["detail"]
    assert len(res["checks"]) == 1  # no point in prompting a model that is not there

    cfg = AgentConfig(llm_base_url="http://llm/v1", llm_model="m")
    client = _llm_server(vision_answer="blue")
    res = run_llm_test(cfg, _providers(cfg, client), http=client)
    assert res["ok"] is True  # vision is optional
    assert res["checks"][-1]["ok"] is False and "misread" in res["checks"][-1]["detail"]


def test_worker_answers_test_requests_from_the_configuration_page():
    r = fakeredis.FakeRedis(decode_responses=True)
    w = Worker(
        AgentConfig(worker_id="w1", llm_provider="fake"),
        r=r,
        backend=SimBackend(r),
        checkpointer=InMemorySaver(),
        sleep=lambda _s: None,
    )
    r.rpush(LLM_TEST_QUEUE, json.dumps({"id": "t1", "values": {"llm_provider": "fake"}}))
    t = threading.Thread(target=w.serve_llm_tests, kwargs={"poll_seconds": 0.05}, daemon=True)
    t.start()
    for _ in range(100):
        raw = r.get(LLM_TEST_RESULT.format(id="t1"))
        if raw:
            break
        threading.Event().wait(0.05)
    w.stop.set()
    t.join(timeout=10)
    res = json.loads(raw)
    assert res["ok"] is True and res["worker_id"] == "w1" and res["provider"] == "fake"
