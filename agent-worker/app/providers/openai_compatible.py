"""OpenAI-compatible chat completions client (vLLM, LM Studio, llama.cpp server, OpenAI...).

Structured output modes (LLM_STRUCTURED_OUTPUT):
- json_schema  : `response_format={"type":"json_schema",...}` (vLLM >= 0.6, OpenAI)
- guided_json  : vLLM `extra_body` field `guided_json` (older vLLM)
- json_object  : `response_format={"type":"json_object"}` (JSON, no schema)
- none         : plain text; the planner extracts/repairs JSON itself

Thinking (LLM_THINKING) for reasoning models served by vLLM (Qwen3, DeepSeek-R1 distills...):
- auto : send nothing (the chat template's default)
- on / off : `chat_template_kwargs={"enable_thinking": ...}`. Off is much faster for planning; reasoning
  text (if any) arrives in `reasoning_content` and is ignored either way.
"""

from __future__ import annotations

import time
from typing import Any

import httpx

from .base import ChatMessage, Completion, LLMError

MAX_RESPONSE_CHARS = 200_000


class OpenAICompatibleProvider:
    name = "openai_compatible"

    def __init__(
        self,
        base_url: str,
        model: str,
        *,
        api_key: str = "",
        temperature: float = 0.2,
        max_tokens: int = 8192,
        timeout: float = 120.0,
        structured_output: str = "json_schema",
        retries: int = 2,
        thinking: str = "auto",
        client: httpx.Client | None = None,
    ) -> None:
        self.model = model
        self.temperature = temperature
        self.max_tokens = max_tokens
        self.structured_output = structured_output
        self.retries = retries
        self.thinking = thinking
        headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
        self.http = client or httpx.Client(base_url=base_url.rstrip("/"), timeout=timeout, headers=headers)

    def _post(self, body: dict[str, Any]) -> Completion:
        delay = 1.0
        last: Exception | None = None
        for attempt in range(self.retries + 1):
            try:
                res = self.http.post("/chat/completions", json=body)
            except httpx.TimeoutException as exc:
                last = LLMError(f"LLM request timed out: {exc}")
            except httpx.TransportError as exc:
                last = LLMError(f"LLM server unreachable: {exc}")
            else:
                if res.status_code >= 500 or res.status_code == 429:
                    last = LLMError(f"LLM server error {res.status_code}")
                elif res.status_code >= 400:
                    # Bad request / auth / unknown model: not worth retrying.
                    raise LLMError(f"LLM rejected the request ({res.status_code}): {res.text[:300]}", retryable=False)
                else:
                    return self._parse(res)
            if attempt < self.retries:
                time.sleep(delay)
                delay *= 2
        assert last is not None
        raise last

    def _parse(self, res: httpx.Response) -> Completion:
        if len(res.content) > MAX_RESPONSE_CHARS * 4:
            raise LLMError("LLM response too large", retryable=False)
        try:
            data = res.json()
            choice = data["choices"][0]
            text = choice["message"].get("content") or ""
        except (ValueError, KeyError, IndexError, TypeError) as exc:
            raise LLMError(f"Malformed LLM response: {exc}") from exc
        usage = data.get("usage") or {}
        return Completion(
            text=text[:MAX_RESPONSE_CHARS],
            model=data.get("model", self.model),
            prompt_tokens=usage.get("prompt_tokens"),
            completion_tokens=usage.get("completion_tokens"),
            finish_reason=choice.get("finish_reason"),
        )

    def _body(self, messages: list[ChatMessage], max_tokens: int | None) -> dict[str, Any]:
        body: dict[str, Any] = {
            "model": self.model,
            "messages": [{"role": m.role, "content": m.content} for m in messages],
            "temperature": self.temperature,
            "max_tokens": max_tokens or self.max_tokens,
        }
        if self.thinking in ("on", "off"):
            body["chat_template_kwargs"] = {"enable_thinking": self.thinking == "on"}
        return body

    def generate_text(self, messages: list[ChatMessage], *, max_tokens: int | None = None) -> Completion:
        return self._post(self._body(messages, max_tokens))

    def generate_structured(
        self, messages: list[ChatMessage], schema: dict[str, Any], *, max_tokens: int | None = None
    ) -> Completion:
        body = self._body(messages, max_tokens)
        mode = self.structured_output
        if mode == "json_schema":
            body["response_format"] = {
                "type": "json_schema",
                "json_schema": {"name": "forma3d_plan", "schema": schema, "strict": False},
            }
        elif mode == "guided_json":
            body["guided_json"] = schema
        elif mode == "json_object":
            body["response_format"] = {"type": "json_object"}
        return self._post(body)
