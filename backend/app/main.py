"""FastAPI application factory."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from redis.asyncio import Redis

from app.api import health, internal
from app.api.v1.router import api_router
from app.core.config import Settings, get_settings
from app.core.errors import register_error_handlers
from app.core.logging import configure_logging, get_logger
from app.database.session import Database
from app.middleware.correlation import CorrelationIdMiddleware
from app.services.rate_limit import MemoryRateLimiter, RateLimiter, RedisRateLimiter
from app.services.storage import ProjectStorage
from app.services.version_service import reconcile_storage

log = get_logger(__name__)


def create_app(
    settings: Settings | None = None,
    *,
    db: Database | None = None,
    redis: Redis | None = None,
    rate_limiter: RateLimiter | None = None,
) -> FastAPI:
    settings = settings or get_settings()
    configure_logging(settings.log_level)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        app.state.db = db or Database(settings.database_url)
        app.state.redis = redis if redis is not None else Redis.from_url(settings.redis_url)
        app.state.rate_limiter = rate_limiter or (
            RedisRateLimiter(app.state.redis) if app.state.redis is not None else MemoryRateLimiter()
        )
        log.info("app.startup", env=settings.app_env)
        try:
            async with app.state.db.sessionmaker() as session:
                await reconcile_storage(
                    session, ProjectStorage(settings.data_root), orphan_minutes=settings.staging_orphan_minutes
                )
        except Exception as exc:  # never block startup on recovery
            log.warning("storage.reconcile_failed", error=type(exc).__name__)
        try:
            yield
        finally:
            if db is None:
                await app.state.db.dispose()
            if redis is None and app.state.redis is not None:
                await app.state.redis.aclose()
            log.info("app.shutdown")

    app = FastAPI(
        title="Forma3D API",
        version="0.1.0",
        lifespan=lifespan,
        docs_url=None if settings.is_production else "/api/docs",
        openapi_url=None if settings.is_production else "/api/openapi.json",
    )
    app.state.settings = settings

    if settings.cors_origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=settings.cors_origins,
            allow_credentials=True,
            allow_methods=["*"],
            allow_headers=["*"],
        )
    app.add_middleware(CorrelationIdMiddleware)
    register_error_handlers(app)

    app.include_router(health.router)
    app.include_router(api_router)
    app.include_router(internal.router)
    return app


app = create_app()
