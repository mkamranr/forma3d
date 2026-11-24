"""Async engine/session management."""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine


class Database:
    def __init__(self, url: str, **engine_kwargs: Any) -> None:
        if url.startswith("postgresql"):
            engine_kwargs.setdefault("pool_pre_ping", True)
            engine_kwargs.setdefault("pool_size", 10)
        self.engine: AsyncEngine = create_async_engine(url, **engine_kwargs)
        self.sessionmaker = async_sessionmaker(self.engine, expire_on_commit=False, class_=AsyncSession)

    async def session(self) -> AsyncIterator[AsyncSession]:
        async with self.sessionmaker() as session:
            yield session

    async def dispose(self) -> None:
        await self.engine.dispose()
