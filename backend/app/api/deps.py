"""FastAPI dependencies (dependency injection of settings, DB sessions, auth, rate limits)."""

from __future__ import annotations

from collections.abc import AsyncIterator, Awaitable, Callable

from fastapi import Depends, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.security import decode_access_token
from app.core.config import Settings
from app.core.errors import AuthError, RateLimitedError
from app.models import User
from app.repositories.users import UserRepository
from app.services.rate_limit import RateLimiter

_bearer = HTTPBearer(auto_error=False)


def get_settings_dep(request: Request) -> Settings:
    return request.app.state.settings


async def get_session(request: Request) -> AsyncIterator[AsyncSession]:
    async with request.app.state.db.sessionmaker() as session:
        yield session


def get_rate_limiter(request: Request) -> RateLimiter:
    return request.app.state.rate_limiter


async def get_current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer),
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings_dep),
) -> User:
    if credentials is None or credentials.scheme.lower() != "bearer":
        raise AuthError("Not authenticated", code="not_authenticated")
    user_id = decode_access_token(credentials.credentials, settings)
    user = await UserRepository(session).get(user_id)
    if user is None or not user.is_active:
        raise AuthError("Invalid access token", code="invalid_token")
    return user


def rate_limit(scope: str, limit_attr: str) -> Callable[..., Awaitable[None]]:
    """Per-client fixed-window limit; `limit_attr` names a Settings field (requests/minute)."""

    async def _dep(
        request: Request,
        limiter: RateLimiter = Depends(get_rate_limiter),
        settings: Settings = Depends(get_settings_dep),
    ) -> None:
        client = request.client.host if request.client else "unknown"
        if not await limiter.hit(f"{scope}:{client}", int(getattr(settings, limit_attr))):
            raise RateLimitedError("Too many requests, please slow down")

    return _dep
