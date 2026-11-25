"""Liveness, readiness and metrics (spec §16)."""

from __future__ import annotations

import hmac

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, PlainTextResponse, Response
from sqlalchemy import text

from app.services.metrics import collect, disk_free_ratio, llm_status

router = APIRouter(tags=["health"])

#: below this free-space ratio on the data volume readiness reports `degraded`
DISK_LOW_RATIO = 0.05


@router.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@router.get("/health/ready")
async def ready(request: Request) -> JSONResponse:
    """503 only when this process cannot serve (database/Redis down). Workers, the LLM and disk are
    reported too but only *degrade* readiness: the workers' own start-up waits for a healthy backend,
    so making them a hard dependency would deadlock the stack."""
    checks: dict[str, str] = {}
    try:
        async with request.app.state.db.engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
        checks["database"] = "ok"
    except Exception:
        checks["database"] = "unavailable"
    redis = request.app.state.redis
    try:
        if redis is None:
            raise RuntimeError("redis not configured")
        await redis.ping()
        checks["redis"] = "ok"
    except Exception:
        checks["redis"] = "unavailable"

    healthy = checks["database"] == "ok" and checks["redis"] == "ok"
    info: dict[str, str] = {}
    if checks["redis"] == "ok":
        try:
            for kind in ("blender", "agent"):
                n = 0
                async for key in redis.scan_iter(match=f"workers:{kind}:*", count=200):
                    k = key.decode() if isinstance(key, bytes) else key
                    n += not k.endswith(":llm")
                info[f"{kind}_workers"] = str(n)
            info["llm"] = await llm_status(redis)
        except Exception:
            info["workers"] = "unknown"
    ratio = disk_free_ratio(request.app.state.settings.data_root)
    if ratio is not None:
        info["disk_free"] = f"{ratio:.0%}"
    degraded = (
        info.get("blender_workers") == "0"
        or info.get("agent_workers") == "0"
        or info.get("llm") == "unavailable"
        or (ratio is not None and ratio < DISK_LOW_RATIO)
    )
    status = "not_ready" if not healthy else ("degraded" if degraded else "ready")
    return JSONResponse(status_code=200 if healthy else 503, content={"status": status, "checks": checks, "info": info})


@router.get("/metrics", include_in_schema=False)
async def metrics(request: Request) -> Response:
    """Prometheus scrape endpoint. Not proxied by the frontend nginx (only `/api/` is); optionally
    protected by METRICS_TOKEN for scrapers on the internal network."""
    token = request.app.state.settings.metrics_token
    if token:
        given = request.headers.get("authorization", "").removeprefix("Bearer ").strip()
        if not hmac.compare_digest(given.encode(), token.encode()):
            return PlainTextResponse("unauthorized", status_code=401)
    async with request.app.state.db.sessionmaker() as session:
        body = await collect(session, request.app.state.redis, request.app.state.settings.data_root)
    return PlainTextResponse(body, media_type="text/plain; version=0.0.4; charset=utf-8")
