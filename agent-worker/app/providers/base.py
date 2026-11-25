"""LLM provider interface. The planner depends only on this protocol, so vLLM, other
OpenAI-compatible servers and the deterministic test provider are interchangeable."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol


@dataclass
class ChatMessage:
    role: str  # system | user | assistant
    content: str
    #: Images for vision-capable models, as data URLs (`data:image/jpeg;base64,...`). User turns only.
    images: list[str] = field(default_factory=list)


@dataclass
class Completion:
    text: str
    model: str
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    finish_reason: str | None = None
    raw: dict[str, Any] = field(default_factory=dict)


class LLMError(Exception):
    """Provider failure. `retryable` distinguishes transient errors (timeouts, 5xx) from bad config."""

    def __init__(self, message: str, *, retryable: bool = True) -> None:
        super().__init__(message)
        self.retryable = retryable


class LLMProvider(Protocol):
    name: str
    model: str

    def generate_structured(
        self, messages: list[ChatMessage], schema: dict[str, Any], *, max_tokens: int | None = None
    ) -> Completion: ...

    def generate_text(self, messages: list[ChatMessage], *, max_tokens: int | None = None) -> Completion: ...
