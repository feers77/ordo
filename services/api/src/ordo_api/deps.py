"""Request context for ordo-api: Environment per request (F2-04)."""

from __future__ import annotations

import os
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Annotated

from fastapi import Depends, Header
from ordo_core import Environment, Registry
from ordo_core.registry import Module
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

_engine: AsyncEngine | None = None
_registry: Registry | None = None


def database_url() -> str:
    url = os.environ.get("ORDO_DATABASE_URL")
    if not url:
        msg = "ORDO_DATABASE_URL no configurada"
        raise RuntimeError(msg)
    return url


def engine() -> AsyncEngine:
    global _engine
    if _engine is None:
        _engine = create_async_engine(database_url(), pool_size=5, max_overflow=5)
    return _engine


def get_registry() -> Registry:
    """Loads the business modules once per process.

    `ORDO_MODULES_PATH` points at the modules directory; without it (or if
    the path does not exist) the service boots with an empty registry, which
    keeps unit environments working but exposes no business models.
    """
    global _registry
    if _registry is None:
        from ordo_core.modules import ModuleLoader

        modules_path = Path(os.environ.get("ORDO_MODULES_PATH", "modules"))
        if modules_path.exists():
            _registry = Registry.build(ModuleLoader([modules_path]).load())
        else:
            _registry = Registry.build([Module("base")])
    return _registry


async def get_session() -> AsyncIterator[AsyncSession]:
    maker = async_sessionmaker(engine(), expire_on_commit=False)
    async with maker() as session:
        yield session


async def get_env(
    session: Annotated[AsyncSession, Depends(get_session)],
    registry: Annotated[Registry, Depends(get_registry)],
    tenant: Annotated[str, Header(alias="X-Ordo-Tenant")],
) -> Environment:
    env = Environment(session=session, tenant=tenant, registry=registry)
    await env.bind()
    return env
