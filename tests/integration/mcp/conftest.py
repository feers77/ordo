"""Servidor MCP contra base real: mismo tenant comercial que el resto."""

import os
import uuid
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from tests.integration.commercial import build_shop

MODULES_ROOT = Path(__file__).resolve().parents[3] / "modules"


def _admin_dsn() -> str:
    pw = os.environ.get("POSTGRES_PASSWORD", "")
    return f"postgresql+asyncpg://ordo:{pw}@127.0.0.1:5432/ordo"


@pytest.fixture(scope="session")
async def mcp_db_url() -> AsyncIterator[str]:
    db_name = f"ordo_mcp_test_{uuid.uuid4().hex[:8]}"
    admin = create_async_engine(_admin_dsn(), isolation_level="AUTOCOMMIT")
    try:
        async with admin.connect() as conn:
            await conn.execute(text(f'CREATE DATABASE "{db_name}"'))
    except Exception:
        pytest.skip("Postgres no disponible (make up)")
    yield _admin_dsn().rsplit("/", 1)[0] + f"/{db_name}"
    async with admin.connect() as conn:
        await conn.execute(text(f'DROP DATABASE IF EXISTS "{db_name}" WITH (FORCE)'))
    await admin.dispose()


@pytest.fixture
async def shop(mcp_db_url: str, monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[dict[str, Any]]:
    """Tenant comercial + servicio MCP apuntando a la misma base."""
    from ordo_mcp import deps

    tenant = f"mcp{uuid.uuid4().hex[:8]}"
    engine = create_async_engine(mcp_db_url)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    session: AsyncSession = maker()
    data = await build_shop(session, tenant, modules_root=MODULES_ROOT)
    data["tenant"] = tenant

    monkeypatch.setenv("ORDO_DATABASE_URL", mcp_db_url)
    monkeypatch.setenv("ORDO_MODULES_PATH", str(MODULES_ROOT))
    monkeypatch.setenv("ORDO_DB_ROLE", "")  # base efímera sin grants del rol app
    # El servicio cachea engine y registry por proceso: se resetean para que
    # tomen la base efímera del test.
    monkeypatch.setattr(deps, "_engine", None)
    monkeypatch.setattr(deps, "_registry", None)

    yield data

    if deps._engine is not None:
        await deps._engine.dispose()
    await session.close()
    await engine.dispose()
