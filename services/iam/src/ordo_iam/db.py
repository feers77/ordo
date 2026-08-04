"""Async engine/session for the IAM database (env: IAM_DATABASE_URL)."""

from __future__ import annotations

import os
from collections.abc import AsyncIterator

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

_engine: AsyncEngine | None = None


def database_url() -> str:
    url = os.environ.get("IAM_DATABASE_URL")
    if not url:
        msg = "IAM_DATABASE_URL no configurada"
        raise RuntimeError(msg)
    return url


def engine() -> AsyncEngine:
    global _engine
    if _engine is None:
        _engine = create_async_engine(database_url(), pool_size=5, max_overflow=5)
    return _engine


async def get_session() -> AsyncIterator[AsyncSession]:
    maker = async_sessionmaker(engine(), expire_on_commit=False)
    async with maker() as session:
        yield session
