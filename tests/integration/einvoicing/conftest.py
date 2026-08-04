"""Entorno de facturación electrónica: módulos instalados y compañía chilena."""

import os
import uuid
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import pytest
from ordo_core import Environment
from ordo_core.installer import ModuleInstaller
from ordo_core.modules import ModuleLoader
from ordo_core.recordset import RecordSet
from ordo_core.registry import Registry
from ordo_core.services.schema import create_kernel_tables
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

MODULES_ROOT = Path(__file__).resolve().parents[3] / "modules"


def _admin_dsn() -> str:
    pw = os.environ.get("POSTGRES_PASSWORD", "")
    return f"postgresql+asyncpg://ordo:{pw}@127.0.0.1:5432/ordo"


@pytest.fixture(scope="session")
async def edi_db_url() -> AsyncIterator[str]:
    db_name = f"ordo_edi_test_{uuid.uuid4().hex[:8]}"
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
async def edi(edi_db_url: str) -> AsyncIterator[dict[str, Any]]:
    """Tenant con base, account y einvoicing instalados, más una compañía."""
    tenant = f"edi{uuid.uuid4().hex[:8]}"
    engine = create_async_engine(edi_db_url)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    session: AsyncSession = maker()
    await session.execute(text(f'CREATE SCHEMA IF NOT EXISTS "t_{tenant}"'))
    await session.commit()

    loader = ModuleLoader([MODULES_ROOT])
    registry = Registry.build(loader.load())
    env = Environment(session=session, tenant=tenant, registry=registry, app_role=None)
    await env.bind()
    await create_kernel_tables(session)

    installer = ModuleInstaller(session, registry, loader.models_by_module)
    manifests = loader.discover()
    for name in ("base", "account", "einvoicing"):
        await installer.install(manifests[name])
    await session.commit()

    currencies = RecordSet(env, "res.currency")
    [currency_id] = await currencies.create([{"name": "CLP", "symbol": "$", "decimal_places": "0"}])
    companies = RecordSet(env, "res.company")
    [company_id] = await companies.create(
        [{"name": "ACME SpA", "currency_id": currency_id, "country_code": "CL"}]
    )
    await session.commit()

    yield {
        "env": env,
        "session": session,
        "company_id": company_id,
        "currency_id": currency_id,
    }

    await session.close()
    await engine.dispose()
