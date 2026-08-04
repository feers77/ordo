"""Crea y puebla un tenant real: schema, módulos, datos mínimos y grants.

Uso (desde la raíz del repo, con el stack arriba):

    POSTGRES_PASSWORD=... uv run python tools/seed_tenant.py demo

Idempotente a nivel de tenant: si el schema ya existe, se niega a tocarlo.
El rol `ordo_app` recibe exactamente los privilegios de datos que necesita;
nunca DDL. Los datos sembrados son de demostración, no un plan contable
revisado (los packs siguen en draft).
"""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from ordo_core import Environment, Registry  # noqa: E402
from ordo_core.installer import ModuleInstaller  # noqa: E402
from ordo_core.modules import ModuleLoader  # noqa: E402
from ordo_core.recordset import RecordSet  # noqa: E402
from ordo_core.services.schema import create_kernel_tables  # noqa: E402
from ordo_core.services.sequences import SequenceService  # noqa: E402
from ordo_core.taxid import rut_check_digit  # noqa: E402

MODULES = ("base", "account", "sale", "purchase", "einvoicing")


def admin_dsn() -> str:
    password = os.environ.get("POSTGRES_PASSWORD")
    if not password:
        raise SystemExit("POSTGRES_PASSWORD requerida (ver infra/compose/.env)")
    host = os.environ.get("ORDO_DB_HOST", "127.0.0.1")
    return f"postgresql+asyncpg://ordo:{password}@{host}:5432/ordo"


def rut(number: int) -> str:
    return f"{number}-{rut_check_digit(number)}"


async def seed(session: AsyncSession, tenant: str) -> None:
    schema = f"t_{tenant}"
    exists = (
        await session.execute(
            text("SELECT 1 FROM information_schema.schemata WHERE schema_name = :s"),
            {"s": schema},
        )
    ).first()
    if exists:
        raise SystemExit(f"El schema {schema} ya existe; este seed no pisa tenants.")

    await session.execute(text(f'CREATE SCHEMA "{schema}"'))
    await session.commit()

    loader = ModuleLoader([REPO_ROOT / "modules"])
    registry = Registry.build(loader.load())
    env = Environment(session=session, tenant=tenant, registry=registry, app_role=None)
    await env.bind()
    await create_kernel_tables(session)
    # La tabla de idempotencia se crea aquí y no lazy: ordo_app no tiene DDL.
    from ordo_core.idempotency import create_table as create_idempotency_table

    await create_idempotency_table(session)

    installer = ModuleInstaller(session, registry, loader.models_by_module)
    manifests = loader.discover()
    for name in MODULES:
        await installer.install(manifests[name])
    await session.commit()
    await env.bind()

    currencies = RecordSet(env, "res.currency")
    [clp] = await currencies.create([{"name": "CLP", "symbol": "$", "decimal_places": "0"}])
    companies = RecordSet(env, "res.company")
    [company] = await companies.create(
        [
            {
                "name": "Demo SpA",
                "currency_id": clp,
                "country_code": "CL",
                "vat": rut(76123456),
            }
        ]
    )
    partners = RecordSet(env, "res.partner")
    await partners.create(
        [
            {"name": "Cliente Demo Ltda", "country_code": "CL", "vat": rut(12345678)},
            {"name": "Proveedor Demo SA", "country_code": "CL", "vat": rut(87654321)},
        ]
    )

    accounts = RecordSet(env, "account.account")
    ids = await accounts.create(
        [
            {
                "code": "1101",
                "name": "Banco",
                "account_type": "asset",
                "company_id": company,
            },
            {
                "code": "1201",
                "name": "Clientes",
                "account_type": "asset",
                "reconcile": True,
                "company_id": company,
            },
            {
                "code": "2101",
                "name": "Proveedores",
                "account_type": "liability",
                "reconcile": True,
                "company_id": company,
            },
            {
                "code": "2105",
                "name": "IVA débito fiscal",
                "account_type": "liability",
                "company_id": company,
            },
            {
                "code": "1105",
                "name": "IVA crédito fiscal",
                "account_type": "asset",
                "company_id": company,
            },
            {"code": "4101", "name": "Ventas", "account_type": "income", "company_id": company},
            {
                "code": "5101",
                "name": "Gastos generales",
                "account_type": "expense",
                "company_id": company,
            },
        ]
    )
    banco, clientes, proveedores, iva_debito, iva_credito, ventas, gastos = ids

    taxes = RecordSet(env, "account.tax")
    await taxes.create(
        [
            {
                "code": "IVA19",
                "name": "IVA 19%",
                "rate": "19",
                "applies_to": "sale",
                "account_id": iva_debito,
                "company_id": company,
            },
            {
                "code": "IVA19C",
                "name": "IVA 19% crédito",
                "rate": "19",
                "applies_to": "purchase",
                "account_id": iva_credito,
                "company_id": company,
            },
        ]
    )
    await RecordSet(env, "account.settings").create(
        [
            {
                "company_id": company,
                "receivable_account_id": clientes,
                "payable_account_id": proveedores,
            }
        ]
    )

    sequences = SequenceService(session)
    for code, name, prefix in (
        ("account.move.sale", "Asientos de venta", "VTA/"),
        ("account.move.purchase", "Asientos de compra", "CMP/"),
        ("account.move.bank", "Asientos de banco", "BCO/"),
    ):
        await sequences.create(code=code, name=name, prefix=prefix, implementation="no_gap")

    await RecordSet(env, "account.journal").create(
        [
            {
                "code": "VTA",
                "name": "Ventas",
                "journal_type": "sale",
                "sequence_code": "account.move.sale",
                "default_account_id": ventas,
                "company_id": company,
            },
            {
                "code": "CMP",
                "name": "Compras",
                "journal_type": "purchase",
                "sequence_code": "account.move.purchase",
                "default_account_id": gastos,
                "company_id": company,
            },
            {
                "code": "BCO",
                "name": "Banco",
                "journal_type": "bank",
                "sequence_code": "account.move.bank",
                "default_account_id": banco,
                "company_id": company,
            },
        ]
    )
    await session.commit()

    # Privilegios de datos para el rol de la aplicación: exactamente lo que
    # necesita para operar, nada de DDL (AGENTS.md §7).
    for statement in (
        f'GRANT USAGE ON SCHEMA "{schema}" TO ordo_app',
        f'GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA "{schema}" TO ordo_app',
        f'GRANT USAGE, SELECT, UPDATE ON ALL SEQUENCES IN SCHEMA "{schema}" TO ordo_app',
        f'ALTER DEFAULT PRIVILEGES IN SCHEMA "{schema}" '
        "GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO ordo_app",
        f'ALTER DEFAULT PRIVILEGES IN SCHEMA "{schema}" '
        "GRANT USAGE, SELECT, UPDATE ON SEQUENCES TO ordo_app",
        # Tablas compartidas del kernel (ir_sequence, ir_job, outbox, idempotencia)
        "GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO ordo_app",
        "GRANT USAGE, SELECT, UPDATE ON ALL SEQUENCES IN SCHEMA public TO ordo_app",
    ):
        await session.execute(text(statement))
    await session.commit()
    print(f"Tenant '{tenant}' listo: módulos {', '.join(MODULES)}, compañía Demo SpA.")
    print(f"Prueba: curl -H 'X-Ordo-Tenant: {tenant}' http://127.0.0.1:3000/api/v1/res.partner")


async def main() -> None:
    if len(sys.argv) != 2:
        raise SystemExit("Uso: uv run python tools/seed_tenant.py <tenant>")
    tenant = sys.argv[1]
    if not tenant.isidentifier() or not tenant.islower():
        raise SystemExit("El tenant debe ser minúsculas, sin espacios ni símbolos.")
    engine = create_async_engine(admin_dsn())
    maker = async_sessionmaker(engine, expire_on_commit=False)
    async with maker() as session:
        await seed(session, tenant)
    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
