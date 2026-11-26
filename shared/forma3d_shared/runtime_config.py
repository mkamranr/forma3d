"""In-app configuration (admin "Configuration" page): what can be changed at runtime, and how workers
learn about changes.

Values set in the app are stored by the backend (table `app_settings`) and override the environment
(.env), which stays the default. Every change bumps the Redis counter CONFIG_VERSION_KEY; workers compare
it before each job and, when it moved, fetch the effective overrides from the backend's internal API
(`GET /internal/v1/config`), so secrets never sit in Redis.
"""

from __future__ import annotations

from typing import Any

CONFIG_VERSION_KEY = "config:version"
#: Agent worker "test connection" requests (backend -> worker, since only the worker can reach the LLM).
LLM_TEST_QUEUE = "config:llm-test"
LLM_TEST_RESULT = "config:llm-test:{id}"

#: Override keys each worker applies (everything else is backend-only).
AGENT_KEYS = (
    "llm_provider",
    "llm_base_url",
    "llm_model",
    "llm_model_vision",
    "llm_model_conversation",
    "llm_model_planning",
    "llm_model_revision",
    "llm_api_key",
    "llm_thinking",
    "llm_structured_output",
    "llm_temperature",
    "llm_max_tokens",
    "llm_timeout_seconds",
    "agent_max_retries",
    "agent_max_revisions",
)
#: Modeling limits (ModelingLimits field names prefixed with `modeling_`), used by backend, agent and Blender.
LIMIT_KEYS = (
    "modeling_max_operations_per_batch",
    "modeling_max_scene_objects",
    "modeling_max_vertices_per_object",
    "modeling_max_scene_vertices",
)


def limit_overrides(values: dict[str, Any]) -> dict[str, Any]:
    """{"modeling_max_scene_objects": 800} -> {"max_scene_objects": 800} (for ModelingLimits.model_copy)."""
    return {k.removeprefix("modeling_"): v for k, v in values.items() if k in LIMIT_KEYS and v is not None}
