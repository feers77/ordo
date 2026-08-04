"""Entorno comercial completo: contabilidad, impuestos, diarios y partner."""

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


def _admin_dsn() -> str:
    pw = os.environ.get("POSTGRES_PASSWORD", "")
    return f"postgresql+asyncpg://ordo:{pw}@127.0.0.1:5432/ordo"


@pytest.fixture(scope="session")
async def sale_db_url() -> AsyncIterator[str]:
    db_name = f"ordo_sale_test_{uuid.uuid4().hex[:8]}"
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
async def shop(sale_db_url: str) -> AsyncIterator[dict[str, Any]]:
    """Tenant con ventas y compras operables de punta a punta."""
    tenant = f"shp{uuid.uuid4().hex[:8]}"
    engine = create_async_engine(sale_db_url)
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
    for name in ("base", "account", "sale", "purchase"):
        await installer.install(manifests[name])
    await session.commit()

    currencies = RecordSet(env, "res.currency")
    [currency_id] = await currencies.create([{"name": "CLP", "symbol": "$", "decimal_places": "0"}])
    companies = RecordSet(env, "res.company")
    [company_id] = await companies.create(
        [{"name": "ACME SpA", "currency_id": currency_id, "country_code": "CL"}]
    )
    partners = RecordSet(env, "res.partner")
    [customer_id, vendor_id] = await partners.create(
        [
            {"name": "Cliente Ltda", "country_code": "CL"},
            {"name": "Proveedor SA", "country_code": "CL"},
        ]
    )

    accounts = RecordSet(env, "account.account")
    account_ids = await accounts.create(
        [
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
            {
                "code": "2105",
                "name": "IVA débito fiscal",
                "account_type": "liability",
                "company_id": company_id,
            },
            {
                "code": "1105",
                "name": "IVA crédito fiscal",
                "account_type": "asset",
                "company_id": company_id,
            },
            {
                "code": "1110",
                "name": "Retenciones por recuperar",
                "account_type": "asset",
                "company_id": company_id,
            },
        ]
    )
    clientes, proveedores, ventas, gastos, iva_debito, iva_credito, retenciones = account_ids

    taxes = RecordSet(env, "account.tax")
    await taxes.create(
        [
            {
                "code": "IVA19",
                "name": "IVA 19%",
                "rate": "19",
                "applies_to": "sale",
                "account_id": iva_debito,
                "company_id": company_id,
            },
            {
                "code": "IVA19C",
                "name": "IVA 19% crédito",
                "rate": "19",
                "applies_to": "purchase",
                "account_id": iva_credito,
                "company_id": company_id,
            },
            {
                "code": "RET10",
                "name": "Retención 10%",
                "rate": "10",
                "is_withholding": True,
                "applies_to": "sale",
                "account_id": retenciones,
                "company_id": company_id,
            },
        ]
    )
    settings = RecordSet(env, "account.settings")
    await settings.create(
        [
            {
                "company_id": company_id,
                "receivable_account_id": clientes,
                "payable_account_id": proveedores,
            }
        ]
    )

    sequences = SequenceService(session)
    await sequences.create(
        code="account.move.sale",
        name="Asientos de venta",
        prefix="VTA/2026/",
        implementation="no_gap",
    )
    await sequences.create(
        code="account.move.purchase",
        name="Asientos de compra",
        prefix="CMP/2026/",
        implementation="no_gap",
    )
    journals = RecordSet(env, "account.journal")
    [sale_journal, purchase_journal] = await journals.create(
        [
            {
                "code": "VTA",
                "name": "Ventas",
                "journal_type": "sale",
                "sequence_code": "account.move.sale",
                "default_account_id": ventas,
                "company_id": company_id,
            },
            {
                "code": "CMP",
                "name": "Compras",
                "journal_type": "purchase",
                "sequence_code": "account.move.purchase",
                "default_account_id": gastos,
                "company_id": company_id,
            },
        ]
    )
    await session.commit()

    yield {
        "env": env,
        "session": session,
        "company_id": company_id,
        "currency_id": currency_id,
        "customer_id": customer_id,
        "vendor_id": vendor_id,
        "sale_journal": sale_journal,
        "purchase_journal": purchase_journal,
        "clientes": clientes,
        "proveedores": proveedores,
        "ventas": ventas,
        "gastos": gastos,
        "iva_debito": iva_debito,
        "iva_credito": iva_credito,
        "retenciones": retenciones,
    }

    await session.close()
    await engine.dispose()
