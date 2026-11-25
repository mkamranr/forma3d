from __future__ import annotations

import os
import socket
from dataclasses import dataclass, field
from pathlib import Path

from .providers.base import LLMProvider


@dataclass
class AgentConfig:
    redis_url: str = "redis://redis:6379/0"
    backend_url: str = "http://backend:8000"
    internal_token: str = "change-me-internal"
    worker_id: str = field(default_factory=lambda: f"agent-{socket.gethostname()}")
    liveness_file: Path = Path("/tmp/worker-alive")
    llm_provider: str = "vllm"  # vllm | openai_compatible | fake
    llm_base_url: str = "http://llm-server:8000/v1"
    llm_model: str = "your-instruct-model"
    llm_api_key: str = ""
    llm_temperature: float = 0.2
    llm_max_tokens: int = 8192
    llm_timeout_seconds: float = 120.0
    llm_structured_output: str = "json_schema"  # json_schema | guided_json | json_object | none
    max_retries: int = 2
    modeling_wait_seconds: int = 600

    @classmethod
    def from_env(cls) -> AgentConfig:
        env = os.environ.get
        return cls(
            redis_url=env("REDIS_URL", cls.redis_url),
            backend_url=env("BACKEND_INTERNAL_URL", cls.backend_url),
            internal_token=env("INTERNAL_SERVICE_TOKEN", cls.internal_token),
            worker_id=env("WORKER_ID", f"agent-{socket.gethostname()}"),
            liveness_file=Path(env("LIVENESS_FILE", "/tmp/worker-alive")),
            llm_provider=env("LLM_PROVIDER", cls.llm_provider).lower(),
            llm_base_url=env("LLM_BASE_URL", cls.llm_base_url),
            llm_model=env("LLM_MODEL_PLANNING") or env("LLM_MODEL", cls.llm_model),
            llm_api_key=env("LLM_API_KEY", ""),
            llm_temperature=float(env("LLM_TEMPERATURE", "0.2")),
            llm_max_tokens=int(env("LLM_MAX_TOKENS", "8192")),
            llm_timeout_seconds=float(env("LLM_TIMEOUT_SECONDS", "120")),
            llm_structured_output=env("LLM_STRUCTURED_OUTPUT", cls.llm_structured_output).lower(),
            max_retries=int(env("AGENT_MAX_RETRIES", "2")),
            modeling_wait_seconds=int(env("JOB_TIMEOUT_SECONDS", "300")) + 300,
        )

    def build_provider(self) -> LLMProvider:
        if self.llm_provider == "fake":
            from .providers.fake import FakeProvider

            return FakeProvider()
        if self.llm_provider in ("vllm", "openai", "openai_compatible"):
            from .providers.openai_compatible import OpenAICompatibleProvider

            return OpenAICompatibleProvider(
                self.llm_base_url,
                self.llm_model,
                api_key=self.llm_api_key,
                temperature=self.llm_temperature,
                max_tokens=self.llm_max_tokens,
                timeout=self.llm_timeout_seconds,
                structured_output=self.llm_structured_output,
            )
        raise ValueError(f"Unknown LLM_PROVIDER {self.llm_provider!r}")
