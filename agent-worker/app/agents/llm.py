"""Shared structured-call helper for the LLM agents: call -> extract JSON -> parse -> check ->
bounded repair with the concrete problems fed back. One provider adapter serves every agent;
each agent may use its own model (LLM_MODEL_<AGENT>)."""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Generic, TypeVar

from pydantic import BaseModel, ValidationError

from ..json_repair import JSONExtractError, extract_json_object
from ..providers.base import ChatMessage, LLMError, LLMProvider

T = TypeVar("T", bound=BaseModel)


@dataclass
class CallRecord:
    agent: str
    attempt: int
    model: str
    duration_ms: int
    ok: bool
    error: str | None = None
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    issues: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {k: v for k, v in self.__dict__.items()}


class AgentFailed(Exception):
    """An agent could not produce a usable answer within its retry budget."""

    def __init__(self, message: str, code: str, calls: list[CallRecord] | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.calls = calls or []


@dataclass
class Result(Generic[T]):
    value: T
    calls: list[CallRecord]
    raw: str


def structured_call(
    agent: str,
    provider: LLMProvider,
    messages: list[ChatMessage],
    schema: dict[str, Any],
    model_cls: type[T],
    *,
    check: Callable[[T], list[str]] | None = None,
    max_retries: int = 2,
    max_tokens: int | None = None,
    on_retry: Callable[[int, list[str]], None] | None = None,
) -> Result[T]:
    calls: list[CallRecord] = []
    problems: list[str] = []
    for attempt in range(1, max_retries + 2):
        t0 = time.monotonic()
        try:
            completion = provider.generate_structured(messages, schema, max_tokens=max_tokens)
        except LLMError as exc:
            calls.append(
                CallRecord(agent, attempt, provider.model, int((time.monotonic() - t0) * 1000), False, str(exc)[:500])
            )
            if not exc.retryable or attempt > max_retries:
                raise AgentFailed(f"The language model is unavailable: {exc}", "llm_unavailable", calls) from exc
            continue
        rec = CallRecord(
            agent,
            attempt,
            completion.model,
            int((time.monotonic() - t0) * 1000),
            True,
            prompt_tokens=completion.prompt_tokens,
            completion_tokens=completion.completion_tokens,
        )
        calls.append(rec)
        try:
            value = model_cls.model_validate(extract_json_object(completion.text))
        except (JSONExtractError, ValidationError) as exc:
            problems = [f"your answer could not be parsed: {str(exc)[:400]}"]
            if completion.finish_reason == "length":
                problems.append("the answer was cut off by the token limit; be more concise")
        else:
            problems = check(value) if check else []
            if not problems:
                return Result(value, calls, completion.text)
        rec.ok, rec.error, rec.issues = False, problems[0][:300], problems[:10]
        if attempt > max_retries:
            break
        if on_retry:
            on_retry(attempt, problems)
        messages = [
            *messages,
            ChatMessage("assistant", completion.text[:8000]),
            ChatMessage(
                "user",
                "Your answer was REJECTED. Fix every problem below and reply again with the complete JSON object "
                "(not only the fixed parts):\n" + "\n".join(f"- {p}" for p in problems[:15]),
            ),
        ]
    raise AgentFailed(
        f"I couldn't work out a valid change ({problems[0] if problems else 'no usable answer'}).",
        "plan_invalid",
        calls,
    )
