"""Puente comercial → documento electrónico.

Convierte una orden de venta ya facturable en el `InvoiceData` neutro que
consumen los adaptadores de país. Aquí es donde faltar un dato deja de ser
un null silencioso: sin identificador tributario del emisor o del receptor
no hay documento electrónico legal que emitir.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from ordo_core.environment import Environment
from ordo_core.recordset import RecordSet

from modules.account.invoicing import ResolvedLine, compute_totals, resolve_taxes
from modules.einvoicing.contracts import InvoiceData, InvoiceLine, Party
from modules.einvoicing.statemachine import EdiError


async def _one(env: Environment, model: str, record_id: int, fields: list[str]) -> dict[str, Any]:
    rows = await RecordSet(env, model).read([record_id], fields=fields)
    if not rows:
        raise EdiError("EDI_SOURCE_NOT_FOUND", f"No existe {model} {record_id}")
    return rows[0]


def _tax_id(value: str | None, who: str) -> str:
    if not value or not value.strip():
        raise EdiError(
            "EDI_MISSING_TAX_ID",
            f"Falta el identificador tributario del {who}",
            hint="Completa el campo vat antes de emitir el documento.",
        )
    return value.strip()


async def invoice_data_from_sale(
    env: Environment, order_id: int, *, document_type_code: str
) -> tuple[InvoiceData, str, int]:
    """Devuelve (datos del documento, país en minúscula, company_id)."""
    order = await _one(
        env,
        "sale.order",
        order_id,
        ["id", "name", "state", "partner_id", "company_id", "currency_id", "date_order"],
    )
    if order["state"] not in ("confirmed", "invoiced"):
        raise EdiError(
            "EDI_SOURCE_NOT_READY",
            "Solo una orden confirmada o facturada emite documento electrónico",
            hint="Confirma la orden primero con action_confirm.",
        )

    company = await _one(env, "res.company", order["company_id"], ["name", "vat", "country_code"])
    partner = await _one(env, "res.partner", order["partner_id"], ["name", "vat", "street", "city"])
    currency = await _one(env, "res.currency", order["currency_id"], ["name", "decimal_places"])
    country = (company["country_code"] or "").lower()
    if not country:
        raise EdiError(
            "EDI_MISSING_COUNTRY",
            "La compañía no declara país; no se puede elegir adaptador",
            hint="Completa country_code en res.company.",
        )

    lines_result = await RecordSet(env, "sale.order.line").search(
        [("order_id", "=", order_id)],
        fields=["id", "name", "quantity", "price_unit", "discount_percent", "tax_codes"],
        limit=500,
    )
    rows = sorted(lines_result["rows"], key=lambda row: row["id"])
    codes = [
        code.strip() for row in rows for code in (row["tax_codes"] or "").split(",") if code.strip()
    ]
    taxes_by_code = await resolve_taxes(
        env, codes, company_id=order["company_id"], side="sale", error_prefix="SALE"
    )
    resolved = [
        ResolvedLine(
            name=row["name"],
            quantity=Decimal(row["quantity"] or "1"),
            price_unit=row["price_unit"],
            discount_percent=Decimal(row["discount_percent"] or "0"),
            taxes=[
                taxes_by_code[code.strip()][1]
                for code in (row["tax_codes"] or "").split(",")
                if code.strip()
            ],
            account_id=None,
        )
        for row in rows
    ]
    decimals = int(currency["decimal_places"] or 2)
    totals = compute_totals(resolved, decimals=decimals)

    invoice = InvoiceData(
        document_type_code=document_type_code,
        issue_date=order["date_order"],
        issuer=Party(
            tax_id=_tax_id(company["vat"], "emisor"),
            name=company["name"],
        ),
        receiver=Party(
            tax_id=_tax_id(partner["vat"], "receptor"),
            name=partner["name"],
            address=partner["street"] or "",
            city=partner["city"] or "",
        ),
        lines=tuple(
            InvoiceLine(
                description=line.name,
                quantity=line.quantity,
                price_unit=line.price_unit,
                discount_percent=line.discount_percent,
            )
            for line in resolved
        ),
        taxes=totals,
        currency=currency["name"],
    )
    return invoice, country, order["company_id"]
