"""Engine, registry and Environment per process (mirrors ordo_mcp.deps).

The worker has no request to hang state on, so engine and registry are
built once per process and every tenant pass gets its own session.
"""

from __future__ import annotations

import os
from pathlib import Path

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
    global _registry
    if _registry is None:
        from ordo_core.modules import ModuleLoader

        modules_path = Path(os.environ.get("ORDO_MODULES_PATH", "modules"))
        if modules_path.exists():
            _registry = Registry.build(ModuleLoader([modules_path]).load())
        else:
            _registry = Registry.build([Module("base")])
    return _registry


def session_maker() -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(engine(), expire_on_commit=False)


async def build_env(session: AsyncSession, tenant: str) -> Environment:
    # ORDO_DB_ROLE vacío desactiva SET ROLE (solo para tests con base efímera);
    # en producción el rol sin privilegios es obligatorio (AGENTS.md §7).
    role = os.environ.get("ORDO_DB_ROLE", "ordo_app") or None
    env = Environment(session=session, tenant=tenant, registry=get_registry(), app_role=role)
    await env.bind()
    return env
