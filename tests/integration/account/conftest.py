"""Entorno contable listo para usar: módulos instalados, compañía y plan mínimo."""

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
from ordo_core.services.sequences import SequenceService
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

MODULES_ROOT = Path(__file__).resolve().parents[3] / "modules"


@pytest.fixture
async def shop(account_db_url: str) -> AsyncIterator[dict[str, Any]]:
    """Tenant comercial completo (ventas, compras, banco), para tesorería."""
    from tests.integration.commercial import build_shop

    tenant = f"tsy{uuid.uuid4().hex[:8]}"
    engine = create_async_engine(account_db_url)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    session: AsyncSession = maker()
    data = await build_shop(session, tenant, modules_root=MODULES_ROOT)
    yield data
    await session.close()
    await engine.dispose()


def _admin_dsn() -> str:
    pw = os.environ.get("POSTGRES_PASSWORD", "")
    return f"postgresql+asyncpg://ordo:{pw}@127.0.0.1:5432/ordo"


@pytest.fixture(scope="session")
async def account_db_url() -> AsyncIterator[str]:
    db_name = f"ordo_account_test_{uuid.uuid4().hex[:8]}"
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
async def books(account_db_url: str) -> AsyncIterator[dict[str, Any]]:
    """Tenant propio con contabilidad instalada, compañía y cuentas básicas."""
    tenant = f"acc{uuid.uuid4().hex[:8]}"
    engine = create_async_engine(account_db_url)
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
    for name in ("base", "account"):
        await installer.install(manifests[name])
    await session.commit()

    currencies = RecordSet(env, "res.currency")
    [currency_id] = await currencies.create([{"name": "CLP", "symbol": "$", "decimal_places": "0"}])
    companies = RecordSet(env, "res.company")
    [company_id] = await companies.create(
        [{"name": "ACME SpA", "currency_id": currency_id, "country_code": "CL"}]
    )

    accounts = RecordSet(env, "account.account")
    account_ids = await accounts.create(
        [
            {"code": "1101", "name": "Caja", "account_type": "asset", "company_id": company_id},
            {
                "code": "1201",
                "name": "Clientes",
                "account_type": "asset",
                "reconcile": True,
                "company_id": company_id,
            },
            {
                "code": "2101",
                "name": "Proveedores",
                "account_type": "liability",
                "reconcile": True,
                "company_id": company_id,
            },
            {"code": "4101", "name": "Ventas", "account_type": "income", "company_id": company_id},
            {
                "code": "5101",
                "name": "Gastos generales",
                "account_type": "expense",
                "company_id": company_id,
            },
        ]
    )

    sequences = SequenceService(session)
    await sequences.create(
        code="account.move.sale",
        name="Asientos de venta",
        prefix="VTA/2026/",
        padding=5,
        implementation="no_gap",
    )
    journals = RecordSet(env, "account.journal")
    [journal_id] = await journals.create(
        [
            {
                "code": "VTA",
                "name": "Ventas",
                "journal_type": "sale",
                "sequence_code": "account.move.sale",
                "company_id": company_id,
            }
        ]
    )
    await session.commit()

    yield {
        "env": env,
        "session": session,
        "company_id": company_id,
        "currency_id": currency_id,
        "journal_id": journal_id,
        "caja": account_ids[0],
        "clientes": account_ids[1],
        "proveedores": account_ids[2],
        "ventas": account_ids[3],
        "gastos": account_ids[4],
    }

    await session.close()
    await engine.dispose()
