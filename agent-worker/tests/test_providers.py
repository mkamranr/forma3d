"""OpenAI-compatible provider and JSON extraction (provider-level behaviour, no agents)."""

from __future__ import annotations

import json

import httpx
import pytest

from app.json_repair import JSONExtractError, extract_json_object
from app.providers.base import ChatMessage, LLMError
from app.providers.openai_compatible import OpenAICompatibleProvider


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
