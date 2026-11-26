"""Settings changed in the app's Configuration page, applied by the agent worker.

Before each job the worker compares the Redis counter `config:version`; when it moved it fetches the
overrides from the backend (internal API) and rebuilds its config, LLM providers and limits. The worker
also answers "Test connection" requests from the Configuration page, because only the agent worker can
reach the LLM server (network isolation).
"""

from __future__ import annotations

import dataclasses
import json
import struct
import time
import zlib
from typing import Any

import httpx
from forma3d_shared.constants.limits import ModelingLimits
from forma3d_shared.runtime_config import AGENT_KEYS, limit_overrides
from pydantic import BaseModel

from .agents.llm import structured_call
from .config import LLM_AGENTS, AgentConfig
from .providers.base import ChatMessage, LLMError, LLMProvider

#: override key -> AgentConfig field
_FIELD = {
    "llm_provider": "llm_provider",
    "llm_base_url": "llm_base_url",
    "llm_model": "llm_model",
    "llm_api_key": "llm_api_key",
    "llm_thinking": "llm_thinking",
    "llm_structured_output": "llm_structured_output",
    "llm_temperature": "llm_temperature",
    "llm_max_tokens": "llm_max_tokens",
    "llm_timeout_seconds": "llm_timeout_seconds",
    "agent_max_retries": "max_retries",
    "agent_max_revisions": "max_revisions",
}


def apply_overrides(base: AgentConfig, values: dict[str, Any]) -> AgentConfig:
    """`base` (from the environment) with the app's overrides on top."""
    changes: dict[str, Any] = {}
    models = dict(base.agent_models)
    for key, value in values.items():
        if key not in AGENT_KEYS or value is None:
            continue
        if key in _FIELD:
            changes[_FIELD[key]] = value
        elif key.startswith("llm_model_"):
            agent = key.removeprefix("llm_model_")
            if agent in LLM_AGENTS:
                if value:
                    models[agent] = value
                else:
                    models.pop(agent, None)  # "" = use the main model
    changes["agent_models"] = models
    if "llm_provider" in changes:
        changes["llm_provider"] = str(changes["llm_provider"]).lower()
    return dataclasses.replace(base, **changes)


def limits_with(values: dict[str, Any]) -> ModelingLimits:
    try:
        return ModelingLimits.from_env().model_copy(update=limit_overrides(values))
    except Exception:
        return ModelingLimits.from_env()


# --------------------------------------------------------------------------- connection test
def _red_png(size: int = 32) -> bytes:
    raw = b"".join(b"\x00" + b"\xd0\x20\x20" * size for _ in range(size))

    def chunk(t: bytes, d: bytes) -> bytes:
        return struct.pack(">I", len(d)) + t + d + struct.pack(">I", zlib.crc32(t + d) & 0xFFFFFFFF)

    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", size, size, 8, 2, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(raw))
        + chunk(b"IEND", b"")
    )


class _Ok(BaseModel):
    ok: bool


def _check(name: str, label: str, fn) -> dict[str, Any]:  # noqa: ANN001
    t0 = time.monotonic()
    try:
        ok, detail = fn()
    except LLMError as exc:
        ok, detail = False, str(exc)[:300]
    except Exception as exc:  # report, never crash the worker
        ok, detail = False, f"{type(exc).__name__}: {exc}"[:300]
    return {"name": name, "label": label, "ok": ok, "detail": detail, "ms": int((time.monotonic() - t0) * 1000)}


def run_llm_test(
    cfg: AgentConfig, providers: dict[str, LLMProvider] | None = None, http: httpx.Client | None = None
) -> dict[str, Any]:
    """Server reachable + model listed, a plain answer, a structured (JSON schema) answer, and image input
    for the vision model. Each check reports ok/detail/latency."""
    result: dict[str, Any] = {
        "provider": cfg.llm_provider,
        "base_url": cfg.llm_base_url,
        "model": cfg.llm_model,
        "vision_model": cfg.model_for("vision"),
        "models": [],
        "checks": [],
    }
    if cfg.llm_provider == "fake":
        result["checks"].append(
            {
                "name": "server",
                "label": "Offline demo planner",
                "ok": True,
                "detail": "No model server is used.",
                "ms": 0,
            }
        )
        result["ok"] = True
        return result
    providers = providers or cfg.build_providers()

    def server() -> tuple[bool, str]:
        headers = {"Authorization": f"Bearer {cfg.llm_api_key}"} if cfg.llm_api_key else {}
        url = f"{cfg.llm_base_url.rstrip('/')}/models"
        res = http.get(url, headers=headers) if http else httpx.get(url, headers=headers, timeout=10.0)
        if res.status_code in (401, 403):
            return False, f"The server refused the API key ({res.status_code})."
        if res.status_code >= 400:
            return False, f"GET /models returned {res.status_code}."
        ids = [m.get("id", "") for m in (res.json().get("data") or []) if isinstance(m, dict)]
        result["models"] = ids[:50]
        missing = [m for m in {cfg.llm_model, *cfg.agent_models.values()} if ids and m not in ids]
        if missing:
            return (
                False,
                f"Reachable, but {', '.join(sorted(missing))} is not served (available: {', '.join(ids[:8])}).",
            )
        return True, f"Reachable; {len(ids)} model(s) served."

    def chat() -> tuple[bool, str]:
        out = providers["conversation"].generate_text(
            [ChatMessage("user", "Reply with the single word OK.")], max_tokens=16
        )
        return bool(out.text.strip()), f"Answered “{out.text.strip()[:40]}” ({out.model})."

    def structured() -> tuple[bool, str]:
        schema = {"type": "object", "properties": {"ok": {"type": "boolean"}}, "required": ["ok"]}
        res = structured_call(
            "test",
            providers["planning"],
            [ChatMessage("user", 'Return the JSON object {"ok": true} and nothing else.')],
            schema,
            _Ok,
            max_retries=0,
            max_tokens=64,
        )
        return res.value.ok, f"Valid JSON with mode “{cfg.llm_structured_output}”."

    def vision() -> tuple[bool, str]:
        from .agents.vision import data_url

        out = providers.get("vision", providers["conversation"]).generate_text(
            [
                ChatMessage(
                    "user",
                    "What colour is this image? Answer with one word.",
                    images=[data_url(_red_png(), "image/png")],
                )
            ],
            max_tokens=16,
        )
        answer = out.text.strip().lower()
        if "red" in answer:
            return True, f"Read a test image correctly ({cfg.model_for('vision')})."
        return False, f"Answered “{answer[:40]}” for a red test image; photos may be misread."

    checks = result["checks"]
    checks.append(_check("server", "Server & model", server))
    if checks[-1]["ok"] or not result["models"]:
        checks.append(_check("chat", "Chat reply", chat))
        checks.append(_check("structured", "Structured output", structured))
        checks.append(_check("vision", "Photo input (image-to-3D)", vision))
    critical = [c for c in checks if c["name"] in ("server", "chat", "structured")]
    result["ok"] = bool(critical) and all(c["ok"] for c in critical) and len(critical) == 3
    return result


def encode(result: dict[str, Any]) -> str:
    return json.dumps(result)[:200_000]
