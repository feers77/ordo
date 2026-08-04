"""Constructor compartido del entorno comercial para tests de integración.

Un tenant con contabilidad, impuestos con cuenta, diarios con secuencia y
partners con RUT válido: lo mínimo para que una orden termine en asiento y,
si hace falta, en documento electrónico.
"""

from typing import Any

from ordo_core import Environment
from ordo_core.installer import ModuleInstaller
from ordo_core.modules import ModuleLoader
from ordo_core.recordset import RecordSet
from ordo_core.registry import Registry
from ordo_core.services.schema import create_kernel_tables
from ordo_core.services.sequences import SequenceService
from ordo_core.taxid import rut_check_digit
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

DEFAULT_MODULES = (
    "base",
    "account",
    "sale",
    "purchase",
    "einvoicing",
    "product",
    "stock",
    "webhook",
)


def rut(number: int) -> str:
    return f"{number}-{rut_check_digit(number)}"


async def build_shop(
    session: AsyncSession,
    tenant: str,
    *,
    modules_root: Any,
    modules: tuple[str, ...] = DEFAULT_MODULES,
) -> dict[str, Any]:
    await session.execute(text(f'CREATE SCHEMA IF NOT EXISTS "t_{tenant}"'))
    await session.commit()

    loader = ModuleLoader([modules_root])
    registry = Registry.build(loader.load())
    env = Environment(session=session, tenant=tenant, registry=registry, app_role=None)
    await env.bind()
    await create_kernel_tables(session)

    installer = ModuleInstaller(session, registry, loader.models_by_module)
    manifests = loader.discover()
    for name in modules:
        await installer.install(manifests[name])
    await session.commit()

    currencies = RecordSet(env, "res.currency")
    [currency_id] = await currencies.create([{"name": "CLP", "symbol": "$", "decimal_places": "0"}])
    companies = RecordSet(env, "res.company")
    [company_id] = await companies.create(
        [
            {
                "name": "ACME SpA",
                "currency_id": currency_id,
                "country_code": "CL",
                "vat": rut(76543210),
            }
        ]
    )
    partners = RecordSet(env, "res.partner")
    [customer_id, vendor_id] = await partners.create(
        [
            {"name": "Cliente Ltda", "country_code": "CL", "vat": rut(12345678)},
            {"name": "Proveedor SA", "country_code": "CL", "vat": rut(87654321)},
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
            {
                "code": "1101",
                "name": "Banco",
                "account_type": "asset",
                "company_id": company_id,
            },
            {
                "code": "1301",
                "name": "Inventario",
                "account_type": "asset",
                "company_id": company_id,
            },
            {
                "code": "2110",
                "name": "Recepciones por facturar",
                "account_type": "liability",
                "company_id": company_id,
            },
            {
                "code": "5201",
                "name": "Costo de venta",
                "account_type": "expense",
                "company_id": company_id,
            },
            {
                "code": "5202",
                "name": "Ajustes de inventario",
                "account_type": "expense",
                "company_id": company_id,
            },
        ]
    )
    (
        clientes,
        proveedores,
        ventas,
        gastos,
        iva_debito,
        iva_credito,
        retenciones,
        banco,
        inventario,
        recepciones,
        costo_venta,
        ajustes_inv,
    ) = account_ids

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
    await sequences.create(
        code="account.move.bank",
        name="Asientos de banco",
        prefix="BCO/2026/",
        implementation="no_gap",
    )
    await sequences.create(
        code="account.move.inventory",
        name="Asientos de inventario",
        prefix="INV/2026/",
        implementation="no_gap",
    )
    journals = RecordSet(env, "account.journal")
    [sale_journal, purchase_journal, bank_journal, inventory_journal] = await journals.create(
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
            {
                "code": "BCO",
                "name": "Banco",
                "journal_type": "bank",
                "sequence_code": "account.move.bank",
                "default_account_id": banco,
                "company_id": company_id,
            },
            {
                "code": "INV",
                "name": "Inventario",
                "journal_type": "general",
                "sequence_code": "account.move.inventory",
                "default_account_id": None,
                "company_id": company_id,
            },
        ]
    )

    warehouses = RecordSet(env, "stock.warehouse")
    [warehouse_id] = await warehouses.create(
        [{"name": "Bodega Central", "code": "BC", "company_id": company_id}]
    )
    stock_locations = RecordSet(env, "stock.location")
    [loc_stock, loc_supplier, loc_customer, loc_loss] = await stock_locations.create(
        [
            {
                "name": "BC/Existencias",
                "location_type": "internal",
                "warehouse_id": warehouse_id,
                "company_id": company_id,
            },
            {
                "name": "Proveedores",
                "location_type": "supplier",
                "warehouse_id": None,
                "company_id": company_id,
            },
            {
                "name": "Clientes",
                "location_type": "customer",
                "warehouse_id": None,
                "company_id": company_id,
            },
            {
                "name": "Ajuste inventario",
                "location_type": "inventory_loss",
                "warehouse_id": None,
                "company_id": company_id,
            },
        ]
    )
    await RecordSet(env, "stock.config").create(
        [
            {
                "company_id": company_id,
                "valuation_account_id": inventario,
                "input_account_id": recepciones,
                "cogs_account_id": costo_venta,
                "loss_account_id": ajustes_inv,
                "journal_id": inventory_journal,
            }
        ]
    )
    products = RecordSet(env, "product.product")
    [product_id, service_id] = await products.create(
        [
            {
                "name": "Notebook 14",
                "default_code": "NB-14",
                "product_type": "consu",
                "company_id": company_id,
            },
            {
                "name": "Soporte anual",
                "default_code": None,
                "product_type": "service",
                "company_id": company_id,
            },
        ]
    )
    await session.commit()

    return {
        "env": env,
        "session": session,
        "company_id": company_id,
        "currency_id": currency_id,
        "customer_id": customer_id,
        "vendor_id": vendor_id,
        "sale_journal": sale_journal,
        "purchase_journal": purchase_journal,
        "bank_journal": bank_journal,
        "clientes": clientes,
        "proveedores": proveedores,
        "ventas": ventas,
        "gastos": gastos,
        "iva_debito": iva_debito,
        "iva_credito": iva_credito,
        "retenciones": retenciones,
        "banco": banco,
        "inventario": inventario,
        "recepciones": recepciones,
        "costo_venta": costo_venta,
        "ajustes_inv": ajustes_inv,
        "inventory_journal": inventory_journal,
        "warehouse_id": warehouse_id,
        "loc_stock": loc_stock,
        "loc_supplier": loc_supplier,
        "loc_customer": loc_customer,
        "loc_loss": loc_loss,
        "product_id": product_id,
        "service_id": service_id,
    }
