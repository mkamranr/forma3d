from __future__ import annotations

from collections.abc import AsyncIterator

import pytest
from fakeredis import FakeAsyncRedis
from httpx import ASGITransport, AsyncClient
from sqlalchemy.pool import StaticPool

import app.models  # noqa: F401  (register tables)
from app.core.config import Settings
from app.database.base import Base
from app.database.session import Database
from app.main import create_app
from app.services.rate_limit import MemoryRateLimiter


@pytest.fixture
def settings() -> Settings:
    return Settings(
        app_env="test",
        database_url="sqlite+aiosqlite://",
        jwt_secret="test-secret-test-secret-test-secret",
        log_level="WARNING",
        rate_limit_auth_per_minute=1000,
    )


@pytest.fixture
async def db() -> AsyncIterator[Database]:
    database = Database("sqlite+aiosqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False})
    async with database.engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield database
    await database.dispose()


@pytest.fixture
async def client(settings: Settings, db: Database) -> AsyncIterator[AsyncClient]:
    application = create_app(settings, db=db, redis=FakeAsyncRedis(), rate_limiter=MemoryRateLimiter())
    async with application.router.lifespan_context(application):
        transport = ASGITransport(app=application)
        async with AsyncClient(transport=transport, base_url="http://test") as c:
            c.app = application  # type: ignore[attr-defined]
            yield c


async def register(
    client: AsyncClient, email: str = "a@example.com", password: str = "s3cret-pass", name: str = "Alice"
) -> dict:
    res = await client.post("/api/v1/auth/register", json={"email": email, "password": password, "display_name": name})
    assert res.status_code == 201, res.text
    return res.json()


def auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}
