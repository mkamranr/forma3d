from __future__ import annotations

import os
import socket
from dataclasses import dataclass, field
from pathlib import Path

from forma3d_shared.secrets import env_secret, with_password

from .providers.base import LLMProvider

#: LLM-backed agents. Each may use its own model (LLM_MODEL_<AGENT>), falling back to LLM_MODEL;
#: they share one provider adapter and endpoint (spec §7.4, §24.3).
LLM_AGENTS = ("conversation", "planning", "revision")


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
    agent_models: dict[str, str] = field(default_factory=dict)  # per-agent overrides
    llm_api_key: str = ""
    llm_temperature: float = 0.2
    llm_max_tokens: int = 8192
    llm_timeout_seconds: float = 120.0
    llm_structured_output: str = "json_schema"  # json_schema | guided_json | json_object | none
    llm_thinking: str = "auto"  # auto | on | off (reasoning models via vLLM chat_template_kwargs)
    max_retries: int = 2  # repair attempts per LLM agent call
    max_revisions: int = 2  # QA -> revision -> rebuild loops per turn
    modeling_wait_seconds: int = 600
    checkpoint_url: str = "memory"

    @classmethod
    def from_env(cls) -> AgentConfig:
        env = os.environ.get
        return cls(
            redis_url=env("REDIS_URL", cls.redis_url),
            backend_url=env("BACKEND_INTERNAL_URL", cls.backend_url),
            internal_token=env_secret("INTERNAL_SERVICE_TOKEN", cls.internal_token),
            worker_id=env("WORKER_ID", f"agent-{socket.gethostname()}"),
            liveness_file=Path(env("LIVENESS_FILE", "/tmp/worker-alive")),
            llm_provider=env("LLM_PROVIDER", cls.llm_provider).lower(),
            llm_base_url=env("LLM_BASE_URL", cls.llm_base_url),
            llm_model=env("LLM_MODEL", cls.llm_model),
            agent_models={a: m for a in LLM_AGENTS if (m := env(f"LLM_MODEL_{a.upper()}"))},
            llm_api_key=env_secret("LLM_API_KEY", ""),
            llm_temperature=float(env("LLM_TEMPERATURE", "0.2")),
            llm_max_tokens=int(env("LLM_MAX_TOKENS", "8192")),
            llm_timeout_seconds=float(env("LLM_TIMEOUT_SECONDS", "120")),
            llm_structured_output=env("LLM_STRUCTURED_OUTPUT", cls.llm_structured_output).lower(),
            llm_thinking=env("LLM_THINKING", cls.llm_thinking).lower(),
            max_retries=int(env("AGENT_MAX_RETRIES", "2")),
            max_revisions=int(env("AGENT_MAX_REVISIONS", "2")),
            modeling_wait_seconds=int(env("JOB_TIMEOUT_SECONDS", "300")) + 300,
            checkpoint_url=with_password(
                env("CHECKPOINT_URL", cls.checkpoint_url),
                env_secret("DATABASE_PASSWORD") or env_secret("POSTGRES_PASSWORD"),
            ),
        )

    def model_for(self, agent: str) -> str:
        return self.agent_models.get(agent) or self.llm_model

    @property
    def step_timeout(self) -> float:
        """Upper bound for one graph node: an LLM agent with all its retries, or a Blender build."""
        llm = self.llm_timeout_seconds * (self.max_retries + 1) * 3  # provider retries transient errors too
        return max(llm, self.modeling_wait_seconds) + 60

    def build_providers(self) -> dict[str, LLMProvider]:
        if self.llm_provider == "fake":
            from .providers.fake import FakeProvider

            fake = FakeProvider()
            return {a: fake for a in LLM_AGENTS}
        if self.llm_provider in ("vllm", "openai", "openai_compatible"):
            from .providers.openai_compatible import OpenAICompatibleProvider

            cache: dict[str, LLMProvider] = {}
            for agent in LLM_AGENTS:
                model = self.model_for(agent)
                if model not in cache:
                    cache[model] = OpenAICompatibleProvider(
                        self.llm_base_url,
                        model,
                        api_key=self.llm_api_key,
                        temperature=self.llm_temperature,
                        max_tokens=self.llm_max_tokens,
                        timeout=self.llm_timeout_seconds,
                        structured_output=self.llm_structured_output,
                        thinking=self.llm_thinking,
                    )
            return {a: cache[self.model_for(a)] for a in LLM_AGENTS}
        raise ValueError(f"Unknown LLM_PROVIDER {self.llm_provider!r}")
