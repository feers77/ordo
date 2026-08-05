"""Crea y puebla un tenant real: schema, módulos, datos mínimos y grants.

Uso (desde la raíz del repo, con el stack arriba):

    POSTGRES_PASSWORD=... uv run python tools/seed_tenant.py demo

Siembra una **tienda de ropa que puede vender el mismo día**: catálogo con
tallas y colores, bodega y sala de ventas con existencias reales, caja con sus
medios de cobro y reglas de reposición. El stock inicial entra por una recepción
validada y no por un INSERT: así nacen las capas de valorización y el balance
cuadra desde el minuto cero.

Idempotente a nivel de tenant: si el schema ya existe, se niega a tocarlo.
El rol `ordo_app` recibe exactamente los privilegios de datos que necesita;
nunca DDL. Los datos sembrados son de demostración, no un plan contable
revisado (los packs siguen en draft).
"""

from __future__ import annotations

import asyncio
import os
import sys
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

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

MODULES = (
    "base",
    "account",
    "product",
    "stock",
    "sale",
    "purchase",
    "einvoicing",
    "webhook",
    "pos",
)

# El catálogo de la tienda: tres modelos, cada uno con su matriz.
CATALOG = (
    {
        "name": "Polera Oversize",
        "code": "POL-OVR",
        "price": "19990",
        "cost": "8000",
        "sizes": ("S", "M", "L", "XL"),
        "colors": (("Negro", "NEG"), ("Blanco", "BLA")),
    },
    {
        "name": "Jeans Recto",
        "code": "JEA-REC",
        "price": "39990",
        "cost": "17000",
        "sizes": ("38", "40", "42"),
        "colors": (("Azul", "AZU"), ("Negro", "NEG")),
    },
)

WAREHOUSE_STOCK = "20"  # unidades por variante en la bodega central
STORE_STOCK = "6"  # unidades por variante en la sala de ventas


def admin_dsn() -> str:
    password = os.environ.get("POSTGRES_PASSWORD")
    if not password:
        raise SystemExit("POSTGRES_PASSWORD requerida (ver infra/compose/.env)")
    host = os.environ.get("ORDO_DB_HOST", "127.0.0.1")
    return f"postgresql+asyncpg://ordo:{password}@{host}:5432/ordo"


def rut(number: int) -> str:
    return f"{number}-{rut_check_digit(number)}"


async def install_modules(session: AsyncSession, tenant: str) -> Environment:
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
    return env


async def seed_accounting(env: Environment) -> dict[str, Any]:
    """Moneda, compañía, plan de cuentas, impuestos, secuencias y diarios."""
    [clp] = await RecordSet(env, "res.currency").create(
        [{"name": "CLP", "symbol": "$", "decimal_places": "0"}]
    )
    [company] = await RecordSet(env, "res.company").create(
        [
            {
                "name": "Demo SpA",
                "currency_id": clp,
                "country_code": "CL",
                "vat": rut(76123456),
            }
        ]
    )
    [customer, vendor, anonymous] = await RecordSet(env, "res.partner").create(
        [
            {"name": "Cliente Demo Ltda", "country_code": "CL", "vat": rut(12345678)},
            {"name": "Textil Sur SA", "country_code": "CL", "vat": rut(87654321)},
            # RUT que la autoridad reserva para consumidor final: sin él no hay
            # boleta anónima, que en retail son casi todas.
            {"name": "Consumidor final", "country_code": "CL", "vat": "66666666-6"},
        ]
    )

    plan = (
        ("1101", "Banco", "asset", False),
        ("1102", "Caja", "asset", False),
        ("1201", "Clientes", "asset", True),
        ("1203", "Deudores por tarjetas", "asset", False),
        ("1301", "Inventario", "asset", False),
        ("2101", "Proveedores", "liability", True),
        ("2105", "IVA débito fiscal", "liability", False),
        ("1105", "IVA crédito fiscal", "asset", False),
        ("2110", "Recepciones por facturar", "liability", False),
        ("4101", "Ventas", "income", False),
        ("5101", "Gastos generales", "expense", False),
        ("5201", "Costo de venta", "expense", False),
        ("5202", "Ajustes de inventario", "expense", False),
        ("5301", "Diferencias de caja", "expense", False),
    )
    ids = await RecordSet(env, "account.account").create(
        [
            {
                "code": code,
                "name": name,
                "account_type": kind,
                "reconcile": reconcile,
                "company_id": company,
            }
            for code, name, kind, reconcile in plan
        ]
    )
    account = dict(zip((row[0] for row in plan), ids, strict=True))

    await RecordSet(env, "account.tax").create(
        [
            {
                "code": "IVA19",
                "name": "IVA 19%",
                "rate": "19",
                "applies_to": "sale",
                "account_id": account["2105"],
                "company_id": company,
            },
            {
                # Precios con IVA incluido: obligatorio en el retail chileno.
                "code": "IVA19I",
                "name": "IVA 19% incluido",
                "rate": "19",
                "applies_to": "sale",
                "price_include": True,
                "account_id": account["2105"],
                "company_id": company,
            },
            {
                "code": "IVA19C",
                "name": "IVA 19% crédito",
                "rate": "19",
                "applies_to": "purchase",
                "account_id": account["1105"],
                "company_id": company,
            },
        ]
    )
    await RecordSet(env, "account.settings").create(
        [
            {
                "company_id": company,
                "receivable_account_id": account["1201"],
                "payable_account_id": account["2101"],
            }
        ]
    )

    sequences = SequenceService(env.session)
    for code, name, prefix in (
        ("account.move.sale", "Asientos de venta", "VTA/"),
        ("account.move.purchase", "Asientos de compra", "CMP/"),
        ("account.move.bank", "Asientos de banco", "BCO/"),
        ("account.move.cash", "Asientos de caja", "CAJ/"),
        ("account.move.inventory", "Asientos de inventario", "INV/"),
    ):
        await sequences.create(code=code, name=name, prefix=prefix, implementation="no_gap")

    journal_plan = (
        ("VTA", "Ventas", "sale", "account.move.sale", account["4101"]),
        ("CMP", "Compras", "purchase", "account.move.purchase", account["5101"]),
        ("BCO", "Banco", "bank", "account.move.bank", account["1101"]),
        ("CAJA", "Caja", "cash", "account.move.cash", account["1102"]),
        ("INV", "Inventario", "general", "account.move.inventory", account["1301"]),
    )
    journal_ids = await RecordSet(env, "account.journal").create(
        [
            {
                "code": code,
                "name": name,
                "journal_type": kind,
                "sequence_code": sequence,
                "default_account_id": default_account,
                "company_id": company,
            }
            for code, name, kind, sequence, default_account in journal_plan
        ]
    )
    journal = dict(zip((row[0] for row in journal_plan), journal_ids, strict=True))
    return {
        "currency": clp,
        "company": company,
        "customer": customer,
        "vendor": vendor,
        "anonymous": anonymous,
        "account": account,
        "journal": journal,
    }


async def seed_warehouses(env: Environment, base: dict[str, Any]) -> dict[str, Any]:
    """Bodega central y tienda: dos ubicaciones internas desde el día uno.

    Con dos, las entregas tienen que decir de dónde salen. Es a propósito: una
    tienda real tiene bodega y sala, y elegir en silencio descuenta de la
    equivocada.
    """
    company = base["company"]
    [warehouse, store] = await RecordSet(env, "stock.warehouse").create(
        [
            {"name": "Bodega Central", "code": "BC", "company_id": company},
            {"name": "Tienda Providencia", "code": "TP", "company_id": company},
        ]
    )
    locations = (
        ("BC/Existencias", "internal", warehouse),
        ("TP/Sala de ventas", "internal", store),
        ("Proveedores", "supplier", None),
        ("Clientes", "customer", None),
        ("Ajuste inventario", "inventory_loss", None),
    )
    ids = await RecordSet(env, "stock.location").create(
        [
            {
                "name": name,
                "location_type": kind,
                "warehouse_id": parent,
                "company_id": company,
            }
            for name, kind, parent in locations
        ]
    )
    location = dict(zip((row[0] for row in locations), ids, strict=True))

    await RecordSet(env, "stock.config").create(
        [
            {
                "company_id": company,
                "valuation_account_id": base["account"]["1301"],
                "input_account_id": base["account"]["2110"],
                "cogs_account_id": base["account"]["5201"],
                "loss_account_id": base["account"]["5202"],
                "journal_id": base["journal"]["INV"],
            }
        ]
    )
    return {"warehouse": warehouse, "store": store, "location": location}


async def seed_pos(env: Environment, base: dict[str, Any], stock: dict[str, Any]) -> int:
    company = base["company"]
    [config] = await RecordSet(env, "pos.config").create(
        [
            {
                "name": "Caja 1",
                "warehouse_id": stock["store"],
                "location_id": stock["location"]["TP/Sala de ventas"],
                "journal_id": base["journal"]["VTA"],
                "cash_journal_id": base["journal"]["CAJA"],
                "cash_account_id": base["account"]["1102"],
                "difference_account_id": base["account"]["5301"],
                "document_type_code": "39",
                "anonymous_partner_id": base["anonymous"],
                "price_includes_tax": True,
                "currency_id": base["currency"],
                "company_id": company,
            }
        ]
    )
    await RecordSet(env, "pos.payment.method").create(
        [
            {
                "name": "Efectivo",
                "code": "EFECTIVO",
                "method_type": "cash",
                "config_id": config,
                "settlement_account_id": base["account"]["1102"],
                "opens_drawer": True,
                "company_id": company,
            },
            {
                "name": "Tarjeta",
                "code": "TARJETA",
                "method_type": "card",
                "config_id": config,
                "settlement_account_id": base["account"]["1203"],
                "opens_drawer": False,
                "company_id": company,
            },
        ]
    )
    for sequence, name, prefix in (
        ("pos.session", "Turnos de caja", "POS/"),
        ("pos.order", "Tickets de punto de venta", "T/"),
    ):
        await SequenceService(env.session).create(code=sequence, name=name, prefix=prefix)
    return config


async def seed_catalog(env: Environment, base: dict[str, Any]) -> list[tuple[int, Decimal]]:
    """Los modelos con su matriz de tallas y colores, ya generada.

    Devuelve pares (variante, costo) para que la carga inicial de stock no tenga
    que adivinar a qué modelo pertenece cada id.
    """
    from modules.product.services import VariantService

    company = base["company"]
    attributes = RecordSet(env, "product.attribute")
    [size_attr, color_attr] = await attributes.create(
        [
            {"name": "Talla", "display_type": "size", "sequence": 10, "company_id": company},
            {"name": "Color", "display_type": "color", "sequence": 20, "company_id": company},
        ]
    )
    [category] = await RecordSet(env, "product.category").create(
        [{"name": "Vestuario", "parent_id": None, "company_id": company}]
    )

    values = RecordSet(env, "product.attribute.value")
    templates = RecordSet(env, "product.template")
    lines = RecordSet(env, "product.template.attribute.line")
    service = VariantService(env)
    variants: list[tuple[int, Decimal]] = []

    for model in CATALOG:
        size_ids = await values.create(
            [
                {
                    "attribute_id": size_attr,
                    "name": size,
                    "code": size,
                    "sequence": (position + 1) * 10,
                    "company_id": company,
                }
                for position, size in enumerate(model["sizes"])
            ]
        )
        color_ids = await values.create(
            [
                {
                    "attribute_id": color_attr,
                    "name": name,
                    "code": code,
                    "sequence": (position + 1) * 10,
                    "company_id": company,
                }
                for position, (name, code) in enumerate(model["colors"])
            ]
        )
        [template] = await templates.create(
            [
                {
                    "name": model["name"],
                    "default_code": model["code"],
                    "category_id": category,
                    "product_type": "consu",
                    "uom_id": None,
                    "list_price": Decimal(model["price"]),
                    "tracking": "none",
                    "income_account_id": base["account"]["4101"],
                    "expense_account_id": None,
                    "description": None,
                    "company_id": company,
                }
            ]
        )
        await lines.create(
            [
                {
                    "template_id": template,
                    "attribute_id": size_attr,
                    "value_ids": ",".join(str(value) for value in size_ids),
                    "sequence": 10,
                    "company_id": company,
                },
                {
                    "template_id": template,
                    "attribute_id": color_attr,
                    "value_ids": ",".join(str(value) for value in color_ids),
                    "sequence": 20,
                    "company_id": company,
                },
            ]
        )
        generated = await service.action_generate_variants(template)
        cost = Decimal(model["cost"])
        variants.extend((product_id, cost) for product_id in generated["product_ids"])

    return variants


async def seed_stock(
    env: Environment,
    base: dict[str, Any],
    stock: dict[str, Any],
    variants: list[tuple[int, Decimal]],
) -> None:
    """Existencias iniciales por recepción validada, nunca por INSERT.

    Así nacen las capas de valorización y el costo promedio de verdad, y el
    balance cuadra desde el minuto cero.
    """
    from modules.stock.services import StockService

    service = StockService(env)
    today = datetime.now(UTC).date().isoformat()

    for destination, quantity in (
        (stock["location"]["BC/Existencias"], WAREHOUSE_STOCK),
        (stock["location"]["TP/Sala de ventas"], STORE_STOCK),
    ):
        picking = await service.create_picking(
            picking_type="in",
            date=today,
            company_id=base["company"],
            partner_id=base["vendor"],
            origin="Carga inicial",
            moves=[
                {
                    "product_id": product_id,
                    "quantity": quantity,
                    "location_from_id": stock["location"]["Proveedores"],
                    "location_to_id": destination,
                    "price_unit": cost,
                }
                for product_id, cost in variants
            ],
        )
        await service.action_validate(picking)


async def seed_reorder_rules(env: Environment, base: dict[str, Any], stock: dict[str, Any]) -> int:
    """Una regla por variante: la tienda se repone desde la bodega."""
    from modules.stock.reorder import ReorderService

    service = ReorderService(env)
    total = 0
    templates = await RecordSet(env, "product.template").search(
        [("company_id", "=", base["company"])], fields=["id"], limit=100
    )
    for template in templates["rows"]:
        result = await service.apply_to_variants(
            template["id"],
            location_id=stock["location"]["TP/Sala de ventas"],
            min_quantity="4",
            max_quantity="12",
            route="internal",
            source_location_id=stock["location"]["BC/Existencias"],
        )
        total += result["created"]
    return total


async def seed_folios(env: Environment, base: dict[str, Any]) -> None:
    """Rangos de folios sin CAF: existen, pero todavía no timbran.

    El CAF es un artefacto criptográfico que emite el SII contra el
    certificado de la empresa. Sembrar uno fabricado dejaría documentos con
    timbre inválido pareciendo válidos, así que se deja el rango vacío y el
    adaptador avisa con CL_DTE_NO_CAF hasta que se cargue el real.
    """
    await RecordSet(env, "edi.folio.range").create(
        [
            {
                "country_code": "cl",
                "document_type_code": code,
                "range_from": 1,
                "range_to": 1000,
                "next_number": 1,
                "authorization_code": None,
                "company_id": base["company"],
            }
            for code in ("33", "39", "61")
        ]
    )


async def grant_app_role(session: AsyncSession, schema: str) -> None:
    """Privilegios de datos para el rol de la aplicación: nada de DDL (§7)."""
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

    env = await install_modules(session, tenant)
    base = await seed_accounting(env)
    stock = await seed_warehouses(env, base)
    await seed_pos(env, base, stock)
    variants = await seed_catalog(env, base)
    await seed_stock(env, base, stock, variants)
    rules = await seed_reorder_rules(env, base, stock)
    await seed_folios(env, base)
    await session.commit()

    await grant_app_role(session, schema)

    print(f"Tenant '{tenant}' listo: {len(MODULES)} módulos, compañía Demo SpA.")
    print(
        f"  Catálogo: {len(variants)} variantes de {len(CATALOG)} modelos, "
        f"{rules} reglas de reposición."
    )
    print(
        f"  Stock: {WAREHOUSE_STOCK} por variante en Bodega Central y "
        f"{STORE_STOCK} en Tienda Providencia."
    )
    print("  Caja 1 lista con efectivo y tarjeta; abre un turno con pos.session.action_open.")
    print("  Folios 33/39/61 creados SIN CAF: cárgalo en authorization_code para timbrar.")
    print(f"  Roles y ACL: uv run python tools/seed_iam_roles.py {tenant}")


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
