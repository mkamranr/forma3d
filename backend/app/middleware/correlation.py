"""Correlation-ID middleware (pure ASGI so it also wraps streaming/SSE responses)."""

from __future__ import annotations

import re
import time
import uuid

import structlog
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.core.logging import get_logger
from app.services.metrics import HTTP

HEADER = "x-request-id"
_VALID = re.compile(r"^[A-Za-z0-9._-]{1,64}$")
log = get_logger("http")


def _route_template(scope: Scope) -> str:
    """Full route template ("/api/v1/projects/{project_id}") for metrics labels: bounded cardinality,
    never raw ids. `scope["route"].path` is relative to its router, so the router prefixes (static
    segments such as /api/v1) are taken from the front of the request path."""
    template = getattr(scope.get("route"), "path", None)
    if not template:
        return "unmatched"
    segments = scope.get("path", "").strip("/").split("/")
    t_segments = template.strip("/").split("/")
    prefix = segments[: max(0, len(segments) - len(t_segments))]
    return "/" + "/".join([*prefix, *t_segments]).strip("/")


class CorrelationIdMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        incoming = dict(scope.get("headers") or []).get(HEADER.encode(), b"").decode("latin-1")
        request_id = incoming if _VALID.match(incoming) else uuid.uuid4().hex
        structlog.contextvars.clear_contextvars()
        structlog.contextvars.bind_contextvars(request_id=request_id)
        started = time.perf_counter()
        status_code = 500

        async def send_wrapper(message: Message) -> None:
            nonlocal status_code
            if message["type"] == "http.response.start":
                status_code = message["status"]
                headers = list(message.get("headers", []))
                headers.append((HEADER.encode(), request_id.encode()))
                message["headers"] = headers
            await send(message)

        try:
            await self.app(scope, receive, send_wrapper)
        finally:
            path = scope.get("path", "")
            elapsed = time.perf_counter() - started
            # Route *templates* only (bounded label set); unmatched paths are pooled.
            route = _route_template(scope)
            if route not in ("/metrics", "/health", "/health/ready"):
                HTTP.observe(scope.get("method", ""), route, status_code, elapsed)
            if not path.startswith("/health"):
                log.info(
                    "http.request",
                    method=scope.get("method"),
                    path=path,
                    status=status_code,
                    duration_ms=round(elapsed * 1000, 1),
                )
            structlog.contextvars.clear_contextvars()
