"""Puente del ticket al documento electrónico.

Espejo de `modules/einvoicing/bridge.py` para el punto de venta. Vive aquí y
no allá porque la flecha apunta de lo específico a lo genérico: `pos` conoce
`einvoicing`, y `einvoicing` no debe enterarse nunca de que existe una caja.

La diferencia real con una factura es el receptor. En retail el 90 % de los
tickets es anónimo, y la boleta se emite igual: con el contacto genérico de la
caja o, si tampoco está, con el identificador de consumidor final del país.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from ordo_core.environment import Environment
from ordo_core.recordset import RecordSet

from modules.account.invoicing import ResolvedLine, compute_totals, resolve_taxes
from modules.einvoicing.contracts import InvoiceData, InvoiceLine, Party
from modules.einvoicing.statemachine import EdiError

# Identificador de consumidor final por país, para el ticket sin cliente.
# No es un valor inventado: es el que la autoridad reserva para eso.
ANONYMOUS_TAX_ID = {"cl": "66666666-6"}


async def _one(env: Environment, model: str, record_id: int, fields: list[str]) -> dict[str, Any]:
    rows = await RecordSet(env, model).read([record_id], fields=fields)
    if not rows:
        raise EdiError("EDI_SOURCE_NOT_FOUND", f"No existe {model} {record_id}")
    return rows[0]


async def _receiver(env: Environment, order: dict[str, Any], country: str) -> Party:
    """El cliente identificado, el contacto genérico de la caja, o consumidor final."""
    partner_id = order["partner_id"]
    if not partner_id:
        [session] = await RecordSet(env, "pos.session").read(
            [order["session_id"]], fields=["config_id"]
        )
        [config] = await RecordSet(env, "pos.config").read(
            [session["config_id"]], fields=["anonymous_partner_id"]
        )
        partner_id = config["anonymous_partner_id"]

    if partner_id:
        partner = await _one(env, "res.partner", partner_id, ["name", "vat", "street", "city"])
        if partner["vat"] and partner["vat"].strip():
            return Party(
                tax_id=partner["vat"].strip(),
                name=partner["name"],
                address=partner["street"] or "",
                city=partner["city"] or "",
            )

    anonymous = ANONYMOUS_TAX_ID.get(country)
    if not anonymous:
        raise EdiError(
            "EDI_MISSING_TAX_ID",
            "El ticket no tiene cliente y el país no declara consumidor final",
            hint=(
                "Fija anonymous_partner_id en la pos.config con un contacto que "
                "tenga vat, o identifica al cliente del ticket."
            ),
        )
    return Party(tax_id=anonymous, name="Consumidor final")


async def invoice_data_from_pos(
    env: Environment,
    order_id: int,
    *,
    document_type_code: str,
) -> tuple[InvoiceData, str, int]:
    """Devuelve (datos del documento, país en minúscula, company_id)."""
    order = await _one(
        env,
        "pos.order",
        order_id,
        [
            "id",
            "name",
            "state",
            "partner_id",
            "session_id",
            "company_id",
            "currency_id",
            "date_order",
            "refund_of_id",
        ],
    )
    if order["state"] != "paid":
        raise EdiError(
            "EDI_SOURCE_NOT_READY",
            "Solo un ticket cobrado emite documento electrónico",
            hint="Valida el ticket con action_validate antes de emitir.",
        )

    company = await _one(env, "res.company", order["company_id"], ["name", "vat", "country_code"])
    currency = await _one(env, "res.currency", order["currency_id"], ["name", "decimal_places"])
    country = (company["country_code"] or "").lower()
    if not country:
        raise EdiError(
            "EDI_MISSING_COUNTRY",
            "La compañía no declara país; no se puede elegir adaptador",
            hint="Completa country_code en res.company.",
        )
    if not company["vat"] or not company["vat"].strip():
        raise EdiError(
            "EDI_MISSING_TAX_ID",
            "Falta el identificador tributario del emisor",
            hint="Completa el campo vat de la compañía antes de emitir.",
        )

    lines_result = await RecordSet(env, "pos.order.line").search(
        [("order_id", "=", order_id)],
        fields=["id", "name", "quantity", "price_unit", "discount_percent", "tax_codes"],
        limit=200,
    )
    rows = sorted(lines_result["rows"], key=lambda row: row["id"])
    codes = [
        code.strip() for row in rows for code in (row["tax_codes"] or "").split(",") if code.strip()
    ]
    taxes_by_code = await resolve_taxes(
        env, codes, company_id=order["company_id"], side="sale", error_prefix="POS"
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

    reference_document = ""
    reference_reason = ""
    if order["refund_of_id"]:
        original = await _one(env, "pos.order", order["refund_of_id"], ["name", "edi_document_id"])
        reference_document = await _reference_to(env, original)
        reference_reason = "Devolución"

    invoice = InvoiceData(
        document_type_code=document_type_code,
        issue_date=order["date_order"],
        issuer=Party(tax_id=company["vat"].strip(), name=company["name"]),
        receiver=await _receiver(env, order, country),
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
        reference_document=reference_document,
        reference_reason=reference_reason,
    )
    return invoice, country, order["company_id"]


async def _reference_to(env: Environment, original: dict[str, Any]) -> str:
    """ "tipo/folio" del documento que la devolución corrige.

    Sin referencia, una nota de crédito no dice qué corrige y la autoridad la
    rechaza. Si el ticket original nunca se boleteó no hay a qué referirse, y
    eso se avisa en vez de emitir una referencia inventada.
    """
    if not original["edi_document_id"]:
        raise EdiError(
            "EDI_REFERENCE_MISSING",
            f"El ticket {original['name']} no tiene documento electrónico que referenciar",
            hint="Emite primero el documento del ticket original.",
        )
    document = await _one(
        env, "edi.document", original["edi_document_id"], ["document_type_code", "number"]
    )
    if not document["number"]:
        raise EdiError(
            "EDI_REFERENCE_MISSING",
            f"El documento del ticket {original['name']} todavía no tiene folio",
            hint="Genera el documento original antes de emitir la devolución.",
        )
    return f"{document['document_type_code']}/{document['number']}"
