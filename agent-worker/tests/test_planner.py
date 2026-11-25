from __future__ import annotations

import json
import uuid

import httpx
import pytest
from forma3d_shared.examples import desk_batch
from forma3d_shared.schemas.agent import AgentContext
from forma3d_shared.schemas.modeling_operation import ALLOWED_OPERATIONS
from forma3d_shared.schemas.scene_object import MaterialSnapshot, ObjectSnapshot, SceneInventory

from app.json_repair import JSONExtractError, extract_json_object
from app.planner import Planner, PlanningFailed, build_messages, compact_scene, system_prompt
from app.prompts.catalogue import NOTES, operation_types
from app.providers.base import ChatMessage, LLMError
from app.providers.fake import FakeProvider, ScriptedProvider
from app.providers.openai_compatible import OpenAICompatibleProvider

PID = uuid.UUID("00000000-0000-4000-8000-000000000002")


def desk_inventory() -> SceneInventory:
    objs = [
        ObjectSnapshot(
            object_id="desk_top",
            name="Desk Top",
            component_type="tabletop",
            location=(0, 0, 0.74),
            dimensions=(1.6, 0.8, 0.04),
            material_ids=["oak"],
        )
    ]
    for lid in ("leg_fl", "leg_fr", "leg_bl", "leg_br"):
        objs.append(
            ObjectSnapshot(
                object_id=lid,
                name=lid,
                component_type="leg",
                parent_id="desk_top",
                dimensions=(0.05, 0.05, 0.72),
                material_ids=["graphite"],
            )
        )
    return SceneInventory(
        objects=objs,
        materials=[
            MaterialSnapshot(material_id="oak", name="Oak"),
            MaterialSnapshot(material_id="graphite", name="Graphite"),
        ],
    )


def ctx(text: str, inv: SceneInventory | None = None, selected: str | None = None) -> AgentContext:
    return AgentContext(
        job_id=uuid.uuid4(),
        project_id=PID,
        project_name="Desk",
        inventory=inv or SceneInventory(),
        user_message=text,
        selected_object_id=selected,
        version_number=1 if inv else None,
    )


def plan_json(intent="modeling", ops=None, reply="ok") -> str:
    return json.dumps({"intent": intent, "reply": reply, "summary": "s", "operations": ops or []})


# ---------------------------------------------------------------- prompt / catalogue
def test_catalogue_covers_every_operation():
    assert set(operation_types()) == set(ALLOWED_OPERATIONS)
    assert set(NOTES) == set(ALLOWED_OPERATIONS)
    prompt = system_prompt()
    for op in ALLOWED_OPERATIONS:
        assert f"- {op} (" in prompt
    assert "{{CATALOGUE}}" not in prompt


def test_messages_embed_scene_request_and_selection():
    msgs = build_messages(ctx("make it walnut", desk_inventory(), selected="desk_top"))
    user = msgs[-1].content
    assert "<scene>" in user and '"id":"desk_top"' in user and "<request>make it walnut</request>" in user
    assert "'Desk Top' (id desk_top) selected" in user
    scene = compact_scene(desk_inventory())
    assert scene["materials"][1] == {
        "id": "graphite",
        "name": "Graphite",
        "color": "#CCCCCC",
        "metallic": 0.0,
        "roughness": 0.5,
        "used_by": 4,
    }


# ---------------------------------------------------------------- JSON repair
@pytest.mark.parametrize(
    "raw",
    [
        '{"a": 1}',
        'Sure! ```json\n{"a": 1}\n```',
        'Here you go: {"a": 1, } trailing text',
        '{\n// comment\n"a": 1}',
        'prefix {"a": {"b": "}"}, "c": 1} suffix',
    ],
)
def test_extract_json_object(raw):
    assert "a" in extract_json_object(raw)


@pytest.mark.parametrize("raw", ["", "no json here", "[1,2]", '{"a": '])
def test_extract_json_object_rejects(raw):
    with pytest.raises(JSONExtractError):
        extract_json_object(raw)


# ---------------------------------------------------------------- planner loop
def test_fake_provider_desk_plan_validates():
    result = Planner(FakeProvider()).plan(ctx("Create a modern wooden desk"))
    assert result.response.intent == "modeling"
    assert {o["target_object_id"] for o in result.operations if o.get("target_object_id")} >= {"desk_top", "leg_br"}
    assert result.stats["new_objects"] == 5


def test_non_modeling_intents_have_no_operations():
    for text, intent in [
        ("export it please", "export"),
        ("how many objects are there?", "question"),
        ("hmm", "clarify"),
    ]:
        result = Planner(FakeProvider()).plan(ctx(text, desk_inventory()))
        assert result.response.intent == intent and result.operations == []


def test_repair_loop_feeds_back_parse_and_validation_errors():
    bad_ref = [
        {
            "operation_id": "a",
            "operation_type": "assign_material",
            "target_object_id": "ghost",
            "parameters": {"material_id": "oak"},
        }
    ]
    good = [
        {
            "operation_id": "a",
            "operation_type": "assign_material",
            "target_object_id": "leg_fl",
            "parameters": {"material_id": "oak"},
            "dependencies": [],
            "metadata": None,
        }
    ]
    provider = ScriptedProvider(["I think the answer is...", plan_json(ops=bad_ref), plan_json(ops=good)])
    progress: list[str] = []
    result = Planner(provider, max_retries=2).plan(ctx("make the leg oak", desk_inventory()), progress.append)
    assert [o["target_object_id"] for o in result.operations] == ["leg_fl"]
    assert len(provider.calls) == 3
    second_feedback = provider.calls[2][-1].content
    assert "REJECTED" in second_feedback and "ghost" in second_feedback
    assert "could not be parsed" in provider.calls[1][-1].content
    assert [c.ok for c in result.calls] == [False, False, True]
    assert any("Fixing" in p for p in progress)


def test_retries_are_bounded():
    provider = ScriptedProvider([plan_json(ops=[])] * 3)
    with pytest.raises(PlanningFailed) as exc:
        Planner(provider, max_retries=2).plan(ctx("add a thing", desk_inventory()))
    assert exc.value.code == "plan_invalid" and len(provider.calls) == 3


def test_code_injection_in_names_is_rejected_by_validator():
    evil = [
        {
            "operation_id": "a",
            "operation_type": "create_cube",
            "target_object_id": "x",
            "parameters": {"name": "__import__('os').system('rm -rf /')"},
        }
    ]
    provider = ScriptedProvider([plan_json(ops=evil)] * 2)
    with pytest.raises(PlanningFailed):
        Planner(provider, max_retries=1).plan(ctx("add a cube"))


def test_non_retryable_llm_error_fails_fast():
    class Broken(ScriptedProvider):
        def generate_structured(self, messages, schema, *, max_tokens=None):
            raise LLMError("unknown model", retryable=False)

    with pytest.raises(PlanningFailed) as exc:
        Planner(Broken([])).plan(ctx("x"))
    assert exc.value.code == "llm_unavailable"


def test_desk_example_is_valid_plan_shape():
    # The canonical example batch must itself be a valid planner answer.
    provider = ScriptedProvider([plan_json(ops=desk_batch()["operations"])])
    assert Planner(provider).plan(ctx("create a desk")).operations


# ---------------------------------------------------------------- OpenAI-compatible provider
def _provider(handler, mode="json_schema", retries=2):
    client = httpx.Client(base_url="http://llm/v1", transport=httpx.MockTransport(handler))
    p = OpenAICompatibleProvider("http://llm/v1", "my-model", structured_output=mode, retries=retries, client=client)
    return p


def _ok(content: str) -> httpx.Response:
    return httpx.Response(
        200,
        json={
            "model": "my-model",
            "choices": [{"message": {"content": content}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 5},
        },
    )


@pytest.mark.parametrize(
    "mode,key",
    [
        ("json_schema", "response_format"),
        ("guided_json", "guided_json"),
        ("json_object", "response_format"),
        ("none", None),
    ],
)
def test_structured_output_modes(mode, key, monkeypatch):
    seen = {}

    def handler(request: httpx.Request):
        seen.update(json.loads(request.content))
        return _ok('{"x":1}')

    out = _provider(handler, mode).generate_structured([ChatMessage("user", "hi")], {"type": "object"})
    assert out.text == '{"x":1}' and out.prompt_tokens == 10
    assert seen["model"] == "my-model"
    if key:
        assert key in seen
    else:
        assert "response_format" not in seen and "guided_json" not in seen


def test_provider_retries_5xx_but_not_4xx(monkeypatch):
    monkeypatch.setattr("app.providers.openai_compatible.time.sleep", lambda _s: None)
    calls = {"n": 0}

    def flaky(request):
        calls["n"] += 1
        return httpx.Response(503) if calls["n"] < 3 else _ok("{}")

    assert _provider(flaky).generate_text([ChatMessage("user", "x")]).text == "{}"
    assert calls["n"] == 3

    calls["n"] = 0

    def bad(request):
        calls["n"] += 1
        return httpx.Response(400, text="model not found")

    with pytest.raises(LLMError) as exc:
        _provider(bad).generate_text([ChatMessage("user", "x")])
    assert not exc.value.retryable and calls["n"] == 1


@pytest.mark.parametrize("thinking,expected", [("auto", None), ("off", False), ("on", True)])
def test_thinking_switch(thinking, expected):
    seen = {}

    def handler(request: httpx.Request):
        seen.update(json.loads(request.content))
        return _ok("{}")

    client = httpx.Client(base_url="http://llm/v1", transport=httpx.MockTransport(handler))
    p = OpenAICompatibleProvider("http://llm/v1", "m", thinking=thinking, client=client)
    p.generate_structured([ChatMessage("user", "x")], {"type": "object"})
    if expected is None:
        assert "chat_template_kwargs" not in seen
    else:
        assert seen["chat_template_kwargs"] == {"enable_thinking": expected}
