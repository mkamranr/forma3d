"""Runtime configuration: the settings an admin can change in the app, layered over the environment.

    effective value = value saved in the app (table app_settings)  >  .env / environment  >  built-in default

The registry (FIELDS) is the single description of every editable setting: type, bounds, group, help text
and who consumes it (backend `Settings`, modeling limits, agent worker, or the browser). The backend applies
overrides per request (cached, re-checked every few seconds against the Redis version counter, so several
backend replicas converge); workers fetch them from the internal API when the counter moves.
"""

from __future__ import annotations

import os
import time
from dataclasses import dataclass, field
from typing import Any, Literal

from redis.asyncio import Redis
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import Settings
from app.core.errors import AppError
from app.core.logging import get_logger
from app.models import AppSetting
from forma3d_shared.constants.limits import ModelingLimits
from forma3d_shared.runtime_config import CONFIG_VERSION_KEY, limit_overrides

log = get_logger(__name__)

FieldType = Literal["string", "url", "int", "float", "bool", "enum", "secret"]
Target = Literal["settings", "limits", "agent", "client"]
REFRESH_SECONDS = 3.0


@dataclass(frozen=True)
class FieldDef:
    key: str
    group: str
    label: str
    help: str
    type: FieldType
    target: Target
    default: Any = None
    options: tuple[tuple[str, str], ...] = ()
    min: float | None = None
    max: float | None = None
    env: str = ""  # environment variable that provides the default
    #: Takes effect only after the service restarts (shown in the UI).
    restart: bool = False
    #: Only meaningful when another field has this value, e.g. ("llm_provider", "fake").
    placeholder: str = ""

    @property
    def env_name(self) -> str:
        return self.env or self.key.upper()


GROUPS: tuple[tuple[str, str, str], ...] = (
    ("ai", "AI model", "The language and vision models the assistant uses. Changes apply to the next request."),
    ("rendering", "Rendering & export", "Blender jobs, renders, thumbnails and the defaults offered in dialogs."),
    ("limits", "Limits & retention", "Scene complexity, request rates and how long data is kept."),
    ("accounts", "Accounts", "Who can sign up, and the people using this installation."),
)

_PROVIDERS = (("vllm", "vLLM"), ("openai_compatible", "OpenAI-compatible"), ("fake", "Offline demo planner"))
_STRUCTURED = (
    ("json_schema", "JSON schema (vLLM ≥ 0.6, OpenAI)"),
    ("guided_json", "guided_json (older vLLM)"),
    ("json_object", "JSON object (no schema)"),
    ("none", "None (plain text)"),
)
_THINKING = (("auto", "Model default"), ("off", "Off (faster)"), ("on", "On"))
_QUALITY = (("draft", "Draft"), ("standard", "Standard"), ("high", "High"))
_SIZES = (
    ("1280x720", "HD 1280×720"),
    ("1920x1080", "Full HD 1920×1080"),
    ("1080x1080", "Square 1080×1080"),
    ("1080x1350", "Portrait 1080×1350"),
)
_FORMATS = (("glb", "GLB"), ("gltf", "glTF (zip)"), ("obj", "OBJ (zip)"), ("stl", "STL"))
_UNITS = (("m", "Meters"), ("cm", "Centimeters"), ("mm", "Millimeters"))

FIELDS: tuple[FieldDef, ...] = (
    # ------------------------------------------------------------------ AI model (agent worker)
    FieldDef(
        "llm_provider",
        "ai",
        "Provider",
        "Where the assistant's language model runs. The offline planner only knows the demo scenarios.",
        "enum",
        "agent",
        "vllm",
        _PROVIDERS,
    ),
    FieldDef(
        "llm_base_url",
        "ai",
        "Endpoint URL",
        "OpenAI-compatible API base, e.g. http://10.0.0.5:8000/v1. Must be reachable from the agent worker.",
        "url",
        "agent",
        "http://llm-server:8000/v1",
    ),
    FieldDef(
        "llm_model",
        "ai",
        "Model",
        "Served model name (vllm serve <name>). Used by every agent unless overridden below.",
        "string",
        "agent",
        "your-instruct-model",
    ),
    FieldDef(
        "llm_model_vision",
        "ai",
        "Vision model",
        "Reads photos attached in chat (image-to-3D). Leave empty to use the main model if it supports images.",
        "string",
        "agent",
        "",
    ),
    FieldDef(
        "llm_api_key",
        "ai",
        "API key",
        "Sent as a bearer token. Stored in the database; never shown again after saving.",
        "secret",
        "agent",
        "",
    ),
    FieldDef(
        "llm_thinking",
        "ai",
        "Reasoning",
        "For reasoning models (Qwen3, R1 distills): off makes plans much faster.",
        "enum",
        "agent",
        "auto",
        _THINKING,
    ),
    FieldDef(
        "llm_structured_output",
        "ai",
        "Structured output",
        "How the server is asked for JSON. Use the first option your server supports.",
        "enum",
        "agent",
        "json_schema",
        _STRUCTURED,
    ),
    FieldDef("llm_temperature", "ai", "Temperature", "Lower is more predictable.", "float", "agent", 0.2, min=0, max=2),
    FieldDef(
        "llm_max_tokens",
        "ai",
        "Max tokens per answer",
        "Plans for big scenes need 2–4k.",
        "int",
        "agent",
        8192,
        min=256,
        max=65536,
    ),
    FieldDef(
        "llm_timeout_seconds",
        "ai",
        "Request timeout (s)",
        "Per model call. Slow servers need more.",
        "int",
        "agent",
        120,
        min=10,
        max=3600,
    ),
    FieldDef(
        "llm_model_conversation",
        "ai",
        "Conversation agent model",
        "Optional override (understands requests).",
        "string",
        "agent",
        "",
    ),
    FieldDef(
        "llm_model_planning",
        "ai",
        "Planning agent model",
        "Optional override (designs the parts).",
        "string",
        "agent",
        "",
    ),
    FieldDef(
        "llm_model_revision",
        "ai",
        "Revision agent model",
        "Optional override (fixes QA findings).",
        "string",
        "agent",
        "",
    ),
    FieldDef(
        "agent_max_retries",
        "ai",
        "Repair attempts",
        "Times an agent may fix an answer the validator rejected.",
        "int",
        "agent",
        2,
        min=0,
        max=5,
    ),
    FieldDef(
        "agent_max_revisions",
        "ai",
        "QA revision rounds",
        "Rebuilds after failed quality checks, per request.",
        "int",
        "agent",
        2,
        min=0,
        max=5,
    ),
    # ------------------------------------------------------------------ rendering & export
    FieldDef(
        "default_render_quality",
        "rendering",
        "Default render quality",
        "Preselected in the Render dialog.",
        "enum",
        "client",
        "standard",
        _QUALITY,
    ),
    FieldDef(
        "default_render_size",
        "rendering",
        "Default render size",
        "Preselected in the Render dialog.",
        "enum",
        "client",
        "1280x720",
        _SIZES,
    ),
    FieldDef(
        "default_export_format",
        "rendering",
        "Default export format",
        "Preselected in the Export dialog.",
        "enum",
        "client",
        "glb",
        _FORMATS,
    ),
    FieldDef(
        "default_export_unit",
        "rendering",
        "Default export unit",
        "Preselected in the Export dialog.",
        "enum",
        "client",
        "m",
        _UNITS,
    ),
    FieldDef(
        "render_eevee_enabled",
        "rendering",
        "Offer Eevee renders",
        "Needs a GPU on the Blender workers; Cycles always works.",
        "bool",
        "settings",
    ),
    FieldDef(
        "render_timeout_seconds",
        "rendering",
        "Render timeout (s)",
        "A render that takes longer is stopped.",
        "int",
        "settings",
        min=10,
        max=3600,
    ),
    FieldDef(
        "job_timeout_seconds",
        "rendering",
        "Modeling job timeout (s)",
        "A Blender build that takes longer is stopped.",
        "int",
        "settings",
        min=10,
        max=3600,
    ),
    FieldDef(
        "render_thumbnails",
        "rendering",
        "Render thumbnails",
        "Rendered previews for versions and the dashboard (off: faster builds, flat previews).",
        "bool",
        "settings",
    ),
    FieldDef("thumbnail_size", "rendering", "Thumbnail size (px)", "", "int", "settings", min=64, max=1024),
    # ------------------------------------------------------------------ limits & retention
    FieldDef(
        "modeling_max_operations_per_batch",
        "limits",
        "Operations per change",
        "Largest single edit the validator accepts.",
        "int",
        "limits",
        min=1,
        max=2000,
    ),
    FieldDef("modeling_max_scene_objects", "limits", "Objects per scene", "", "int", "limits", min=1, max=5000),
    FieldDef(
        "modeling_max_vertices_per_object", "limits", "Vertices per object", "", "int", "limits", min=8, max=5_000_000
    ),
    FieldDef("modeling_max_scene_vertices", "limits", "Vertices per scene", "", "int", "limits", min=8, max=20_000_000),
    FieldDef("max_pending_outputs", "limits", "Queued renders/exports per user", "", "int", "settings", min=1, max=100),
    FieldDef(
        "rate_limit_jobs_per_minute",
        "limits",
        "Requests per minute (chat, edits, uploads)",
        "Per client address.",
        "int",
        "settings",
        min=1,
        max=10_000,
    ),
    FieldDef(
        "rate_limit_auth_per_minute",
        "limits",
        "Sign-in attempts per minute",
        "Per client address.",
        "int",
        "settings",
        min=1,
        max=10_000,
    ),
    FieldDef(
        "deleted_project_retention_days",
        "limits",
        "Keep deleted projects (days)",
        "Then their files and history are purged. 0 keeps them forever.",
        "int",
        "settings",
        min=0,
        max=3650,
    ),
    FieldDef(
        "output_retention_days",
        "limits",
        "Keep renders & exports (days)",
        "Users can render/export again later. 0 keeps them forever.",
        "int",
        "settings",
        min=0,
        max=3650,
    ),
    FieldDef(
        "agent_trace_prompts",
        "limits",
        "Keep full LLM prompts in agent traces",
        "Stores the exact prompt and raw answer of every LLM call so administrators can inspect them under "
        '"How this was made". Users always see step summaries only.',
        "bool",
        "settings",
    ),
    FieldDef(
        "agent_trace_prompt_retention_days",
        "limits",
        "Keep LLM prompts (days)",
        "After this, prompts are removed from traces; the step summaries stay. 0 keeps them forever.",
        "int",
        "settings",
        min=0,
        max=3650,
    ),
    # ------------------------------------------------------------------ accounts
    FieldDef(
        "registration_open",
        "accounts",
        "Allow sign-ups",
        "When off, only existing accounts can sign in (the first account can always be created).",
        "bool",
        "settings",
    ),
)
BY_KEY = {f.key: f for f in FIELDS}
CLIENT_KEYS = tuple(f.key for f in FIELDS if f.target == "client") + ("registration_open",)


# --------------------------------------------------------------------------- validation
def coerce(f: FieldDef, value: Any) -> Any:
    """Validate and normalise one value for field `f` (AppError 422 with a readable message)."""

    def bad(msg: str) -> AppError:
        return AppError(f"{f.label}: {msg}", code="invalid_setting", status_code=422)

    if f.type == "bool":
        if not isinstance(value, bool):
            raise bad("must be on or off")
        return value
    if f.type in ("int", "float"):
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise bad("must be a number")
        if f.type == "int":
            if float(value) != int(value):
                raise bad("must be a whole number")
            value = int(value)
        else:
            value = float(value)
        if f.min is not None and value < f.min:
            raise bad(f"must be at least {f.min:g}")
        if f.max is not None and value > f.max:
            raise bad(f"must be at most {f.max:g}")
        return value
    if not isinstance(value, str):
        raise bad("must be text")
    value = value.strip()
    if len(value) > 500 or any(c in value for c in "\r\n\x00"):
        raise bad("is too long or contains line breaks")
    if f.type == "enum" and value not in {o for o, _ in f.options}:
        raise bad(f"must be one of {', '.join(o for o, _ in f.options)}")
    if f.type == "url":
        if not value.startswith(("http://", "https://")) or " " in value:
            raise bad("must be an http(s) URL")
        value = value.rstrip("/")
    return value


def _parse_env(f: FieldDef, raw: str) -> Any:
    try:
        if f.type == "bool":
            return raw.strip().lower() in ("1", "true", "yes", "on")
        if f.type == "int":
            return int(raw)
        if f.type == "float":
            return float(raw)
    except ValueError:
        return f.default
    return raw


# --------------------------------------------------------------------------- store
@dataclass
class RuntimeConfig:
    """Per-process view of the overrides (one per backend app)."""

    base: Settings
    overrides: dict[str, Any] = field(default_factory=dict)
    version: int = -1
    checked_at: float = 0.0
    settings: Settings | None = None
    limits: ModelingLimits = field(default_factory=ModelingLimits.from_env)

    def __post_init__(self) -> None:
        self._apply({})

    def _apply(self, overrides: dict[str, Any]) -> None:
        self.overrides = overrides
        updates = {k: v for k, v in overrides.items() if k in BY_KEY and BY_KEY[k].target in ("settings", "client")}
        # No overrides: the base object itself (so code holding app.state.settings sees the same values).
        self.settings = self.base.model_copy(update=updates) if updates else self.base
        try:
            self.limits = ModelingLimits.from_env().model_copy(update=limit_overrides(overrides))
        except Exception:  # never fall over on a bad stored value
            self.limits = ModelingLimits.from_env()
        global CURRENT
        CURRENT = self

    async def refresh(
        self, sessionmaker: async_sessionmaker[AsyncSession] | None, redis: Redis | None, *, force: bool = False
    ) -> None:
        now = time.monotonic()
        if not force and self.version >= 0 and now - self.checked_at < REFRESH_SECONDS:
            return
        self.checked_at = now
        version = 0
        if redis is not None:
            try:
                version = int(await redis.get(CONFIG_VERSION_KEY) or 0)
            except Exception:
                version = max(self.version, 0)
        if not force and version == self.version:
            return
        if sessionmaker is None:
            return
        try:
            async with sessionmaker() as session:
                rows = (await session.execute(select(AppSetting))).scalars().all()
        except Exception as exc:  # e.g. table missing before migrations ran
            log.warning("config.load_failed", error=type(exc).__name__)
            return
        self._apply({r.key: r.value for r in rows if r.key in BY_KEY})
        self.version = version

    # ------------------------------------------------------------------ admin view
    def default_of(self, f: FieldDef) -> tuple[Any, str]:
        """(value, source) without app overrides: environment, else the built-in default."""
        raw = os.environ.get(f.env_name)
        if f.target in ("settings", "client"):
            return getattr(self.base, f.key), ("env" if raw is not None else "default")
        if f.target == "limits":
            return getattr(ModelingLimits.from_env(), f.key.removeprefix("modeling_")), (
                "env" if raw is not None else "default"
            )
        if raw is not None:
            return _parse_env(f, raw), "env"
        return f.default, "default"

    def describe(self) -> dict[str, Any]:
        groups = []
        for gid, label, help_text in GROUPS:
            items = []
            for f in (x for x in FIELDS if x.group == gid):
                default, default_source = self.default_of(f)
                overridden = f.key in self.overrides
                value = self.overrides[f.key] if overridden else default
                item: dict[str, Any] = {
                    "key": f.key,
                    "label": f.label,
                    "help": f.help,
                    "type": f.type,
                    "options": [{"value": v, "label": lbl} for v, lbl in f.options],
                    "min": f.min,
                    "max": f.max,
                    "source": "app" if overridden else default_source,
                    "env": f.env_name,
                }
                if f.type == "secret":
                    item.update(value=None, is_set=bool(value), default_is_set=bool(default))
                else:
                    item.update(value=value, default=default)
                items.append(item)
            groups.append({"id": gid, "label": label, "help": help_text, "fields": items})
        return {"version": self.version, "groups": groups}

    def client(self) -> dict[str, Any]:
        s = self.settings
        assert s is not None
        return {k: getattr(s, k) for k in CLIENT_KEYS}

    async def save(
        self,
        session: AsyncSession,
        redis: Redis | None,
        sessionmaker: async_sessionmaker[AsyncSession] | None,
        changes: dict[str, Any],
        user_id: Any,
    ) -> None:
        """`changes`: key -> new value, or None to go back to the .env/default. Secrets: "" clears."""
        clean: dict[str, Any] = {}
        for key, value in changes.items():
            f = BY_KEY.get(key)
            if f is None:
                raise AppError(f"Unknown setting {key!r}", code="invalid_setting", status_code=422)
            clean[key] = None if value is None else coerce(f, value)
        for key, value in clean.items():
            if value is None:
                await session.execute(delete(AppSetting).where(AppSetting.key == key))
            else:
                row = await session.get(AppSetting, key)
                if row is None:
                    session.add(AppSetting(key=key, value=value, updated_by=user_id))
                else:
                    row.value, row.updated_by = value, user_id
        await session.commit()
        if redis is not None:
            await redis.incr(CONFIG_VERSION_KEY)
        log.info("config.changed", keys=sorted(clean), user_id=str(user_id))
        await self.refresh(sessionmaker, redis, force=True)

    def agent_values(self, candidate: dict[str, Any] | None = None) -> dict[str, Any]:
        """Effective agent settings for a connection test: saved overrides + unsaved form values."""
        values = {k: v for k, v in self.overrides.items() if BY_KEY.get(k) and BY_KEY[k].target == "agent"}
        for key, value in (candidate or {}).items():
            f = BY_KEY.get(key)
            if f is None or f.target != "agent":
                continue
            if value is None:
                values.pop(key, None)
            else:
                values[key] = coerce(f, value)
        return values


CURRENT: RuntimeConfig | None = None


def current_limits() -> ModelingLimits:
    """Modeling limits with app overrides (sync accessor for the validator call sites)."""
    return CURRENT.limits if CURRENT is not None else ModelingLimits.from_env()
