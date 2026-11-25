"""Application configuration loaded from environment variables."""

from __future__ import annotations

from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


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

    rate_limit_auth_per_minute: int = 20
    rate_limit_jobs_per_minute: int = 10

    cors_origins: list[str] = Field(default_factory=list)

    @property
    def is_production(self) -> bool:
        return self.app_env.lower() == "production"


@lru_cache
def get_settings() -> Settings:
    return Settings()
