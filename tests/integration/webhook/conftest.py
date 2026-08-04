"""Entorno de webhooks: el tenant comercial completo más el módulo de entregas."""

import os
import uuid
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from tests.integration.commercial import DEFAULT_MODULES, build_shop

MODULES_ROOT = Path(__file__).resolve().parents[3] / "modules"
# el tenant comercial no instala webhooks por defecto: aquí sí hacen falta
MODULES = DEFAULT_MODULES if "webhook" in DEFAULT_MODULES else (*DEFAULT_MODULES, "webhook")


def _admin_dsn() -> str:
    pw = os.environ.get("POSTGRES_PASSWORD", "")
    return f"postgresql+asyncpg://ordo:{pw}@127.0.0.1:5432/ordo"


@pytest.fixture(scope="session")
async def webhook_db_url() -> AsyncIterator[str]:
    db_name = f"ordo_wh_test_{uuid.uuid4().hex[:8]}"
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
async def shop(webhook_db_url: str) -> AsyncIterator[dict[str, Any]]:
    tenant = f"wh{uuid.uuid4().hex[:8]}"
    engine = create_async_engine(webhook_db_url)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    session: AsyncSession = maker()
    data = await build_shop(session, tenant, modules_root=MODULES_ROOT, modules=MODULES)
    yield data
    await session.close()
    await engine.dispose()
