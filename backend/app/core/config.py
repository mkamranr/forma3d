"""Application configuration loaded from environment variables."""

from __future__ import annotations

from functools import lru_cache

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from forma3d_shared.secrets import file_secret, with_password


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore", case_sensitive=False)

    app_env: str = "development"
    app_name: str = "forma3d-backend"
    log_level: str = "INFO"

    database_url: str = "postgresql+asyncpg://forma3d:change-me@postgres:5432/forma3d"
    redis_url: str = "redis://redis:6379/0"

    jwt_secret: str = Field(default="dev-insecure-secret-change-me", min_length=16)
    jwt_algorithm: str = "HS256"
    access_token_ttl_seconds: int = 900
    refresh_token_ttl_seconds: int = 1_209_600
    refresh_cookie_name: str = "f3d_refresh"
    refresh_cookie_path: str = "/api/v1/auth"
    cookie_secure: bool = False

    internal_service_token: str = "change-me-internal"
    data_root: str = "/data"

    # Modeling pipeline
    render_thumbnails: bool = True
    thumbnail_size: int = Field(default=384, ge=64, le=2048)
    job_timeout_seconds: int = Field(default=300, ge=10, le=3600)
    # Eevee needs a GPU / OpenGL context; CPU-only containers keep it off (spec §9.6).
    render_eevee_enabled: bool = False
    render_timeout_seconds: int = Field(default=900, ge=10, le=3600)
    #: queued/running renders + exports per user
    max_pending_outputs: int = Field(default=4, ge=1, le=100)
    staging_orphan_minutes: int = 60

    # Phase 8: recovery and retention (app/services/maintenance.py)
    maintenance_enabled: bool = True
    maintenance_interval_seconds: int = Field(default=60, ge=5, le=3600)
    #: extra time past a job's own timeout before the sweeper fails it (worker kill + upload)
    job_grace_seconds: int = Field(default=300, ge=30, le=7200)
    #: a whole agent turn (LLM calls, revisions, Blender runs)
    agent_job_timeout_seconds: int = Field(default=3600, ge=60, le=86400)
    #: jobs nobody picked up (workers down) fail after this long, so the UI does not wait forever
    queued_job_timeout_seconds: int = Field(default=3600, ge=60, le=86400)
    #: a staged result the agent never committed or discarded
    staged_job_timeout_seconds: int = Field(default=1800, ge=60, le=86400)
    #: soft-deleted projects (files + rows) are purged after this many days; 0 disables
    deleted_project_retention_days: int = Field(default=30, ge=0, le=3650)
    #: render/export files are removed after this many days (versions are never purged); 0 disables
    output_retention_days: int = Field(default=30, ge=0, le=3650)
    #: Agent trace ("How this was made"): store the full LLM prompts/responses (admins only) and for how long.
    agent_trace_prompts: bool = True
    agent_trace_prompt_retention_days: int = Field(default=14, ge=0, le=3650)
    #: optional bearer token for GET /metrics (the endpoint is never proxied publicly)
    metrics_token: str = ""

    rate_limit_auth_per_minute: int = Field(default=20, ge=1, le=10_000)
    rate_limit_jobs_per_minute: int = Field(default=10, ge=1, le=10_000)

    # --- accounts (changeable in the app's Configuration page)
    #: anyone may create an account; the very first account is always allowed (and becomes admin)
    registration_open: bool = True
    #: comma-separated emails that are admins (promoted at startup and on registration)
    admin_emails: str = ""

    # --- defaults offered by the render / export dialogs
    default_render_quality: str = "standard"
    default_render_size: str = "1280x720"
    default_export_format: str = "glb"
    default_export_unit: str = "m"

    cors_origins: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _secrets(self) -> Settings:
        """JWT_SECRET, INTERNAL_SERVICE_TOKEN and DATABASE_PASSWORD may come from `NAME_FILE` or
        `/run/secrets/name` (Compose secrets) instead of plain environment variables."""
        for field, env_name in (("jwt_secret", "JWT_SECRET"), ("internal_service_token", "INTERNAL_SERVICE_TOKEN")):
            value = file_secret(env_name)  # plain env vars are already read by pydantic-settings
            if value:
                object.__setattr__(self, field, value)
        password = file_secret("DATABASE_PASSWORD") or file_secret("POSTGRES_PASSWORD")
        if password:
            object.__setattr__(self, "database_url", with_password(self.database_url, password))
        if self.is_production:
            weak = [
                n
                for n, v in (("JWT_SECRET", self.jwt_secret), ("INTERNAL_SERVICE_TOKEN", self.internal_service_token))
                if "change-me" in v or len(v) < 24
            ]
            if weak:
                raise ValueError(f"production needs strong secrets (24+ chars, not the defaults): {', '.join(weak)}")
        return self

    @property
    def admin_email_list(self) -> set[str]:
        return {e.strip().lower() for e in self.admin_emails.split(",") if e.strip()}

    @property
    def is_production(self) -> bool:
        return self.app_env.lower() == "production"


@lru_cache
def get_settings() -> Settings:
    return Settings()
