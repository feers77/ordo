"""Ciclo de vida de la orden de compra: confirmar, registrar factura, cancelar.

Espejo de ventas: el gasto y el IVA crédito van al debe, el proveedor al
haber. La factura del proveedor exige su `vendor_ref`: sin número de
documento del emisor no hay respaldo tributario que registrar.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from ordo_core.environment import Environment
from ordo_core.recordset import RecordSet
from ordo_core.services.sequences import SequenceService

from modules.account.invoicing import (
    ResolvedLine,
    build_invoice_lines,
    compute_totals,
    resolve_taxes,
    settings_for,
)
from modules.account.services import AccountingError, AccountingService

SEQUENCE_CODE = "purchase.order"


class PurchaseError(AccountingError):
    """Error de compras con código estable."""


class PurchaseService:
    def __init__(self, env: Environment) -> None:
        self.env = env
        self.orders = RecordSet(env, "purchase.order")
        self.lines = RecordSet(env, "purchase.order.line")
        self.accounting = AccountingService(env)

    async def create_order(
        self,
        *,
        partner_id: int,
        date_order: str,
        currency_id: int,
        journal_id: int,
        company_id: int,
        lines: list[dict[str, Any]],
        note: str | None = None,
    ) -> int:
        if not lines:
            raise PurchaseError(
                "PURCHASE_ORDER_EMPTY",
                "Una orden de compra necesita al menos una línea",
            )
        [order_id] = await self.orders.create(
            [
                {
                    "partner_id": partner_id,
                    "date_order": date_order,
                    "currency_id": currency_id,
                    "journal_id": journal_id,
                    "company_id": company_id,
                    "note": note,
                    "state": "draft",
                }
            ]
        )
        await self.lines.create(
            [
                {
                    "order_id": order_id,
                    "name": line["name"],
                    "product_id": line.get("product_id"),
                    "quantity": str(line.get("quantity", "1")),
                    "price_unit": line["price_unit"],
                    "discount_percent": str(line.get("discount_percent", "0")),
                    "tax_codes": line.get("tax_codes", ""),
                    "expense_account_id": line.get("expense_account_id"),
                    "company_id": company_id,
                }
                for line in lines
            ]
        )
        return order_id

    async def action_confirm(self, order_id: int) -> str:
        """Fija los totales y asigna el número. Devuelve el número."""
        order = await self._get(order_id)
        self._expect(order, "draft", "confirmar")

        resolved, _ = await self._resolve_lines(order)
        decimals = await self._decimals(order["currency_id"])
        totals = compute_totals(resolved, decimals=decimals)

        sequences = SequenceService(self.env.session)
        await sequences.create(code=SEQUENCE_CODE, name="Órdenes de compra", prefix="PO/")
        number = await sequences.next_by_code(SEQUENCE_CODE)

        await self.orders.write(
            [order_id],
            {
                "state": "confirmed",
                "name": number,
                "amount_untaxed": totals.base,
                "amount_tax": totals.total_included - totals.base,
                "amount_total": totals.total_included,
            },
        )
        return number

    async def action_bill(self, order_id: int, *, vendor_ref: str) -> int:
        """Registra la factura del proveedor y la asienta. Devuelve el move."""
        order = await self._get(order_id)
        self._expect(order, "confirmed", "facturar")
        if not vendor_ref.strip():
            raise PurchaseError(
                "PURCHASE_VENDOR_REF_REQUIRED",
                "Falta el número de la factura del proveedor",
                hint="Pasa vendor_ref con el número del documento recibido.",
            )

        resolved, taxes_by_code = await self._resolve_lines(order)
        decimals = await self._decimals(order["currency_id"])
        totals = compute_totals(resolved, decimals=decimals)

        settings = await settings_for(self.env, order["company_id"])
        if settings["payable_account_id"] is None:
            raise PurchaseError(
                "ACCOUNT_SETTINGS_MISSING",
                "La configuración contable no define la cuenta por pagar",
                hint="Fija payable_account_id en account.settings.",
            )
        journals = RecordSet(self.env, "account.journal")
        [journal] = await journals.read([order["journal_id"]], fields=["default_account_id"])

        move_lines = build_invoice_lines(
            kind="vendor",
            resolved_lines=resolved,
            totals=totals,
            taxes_by_code=taxes_by_code,
            counterpart_account_id=settings["payable_account_id"],
            partner_id=order["partner_id"],
            fallback_account_id=journal["default_account_id"],
            error_prefix="PURCHASE",
            decimals=decimals,
        )
        move_id = await self.accounting.create_move(
            journal_id=order["journal_id"],
            move_date=order["date_order"],
            currency_id=order["currency_id"],
            company_id=order["company_id"],
            lines=move_lines,
            ref=f"Factura proveedor {vendor_ref}",
            partner_id=order["partner_id"],
        )
        await self.accounting.action_post(move_id)
        await self.orders.write(
            [order_id],
            {"state": "billed", "bill_move_id": move_id, "vendor_ref": vendor_ref},
        )
        return move_id

    async def action_credit_note(
        self, order_id: int, *, reason: str, credit_date: str | None = None
    ) -> int:
        """Registra la nota de crédito del proveedor revirtiendo su factura."""
        order = await self._get(order_id)
        self._expect(order, "billed", "acreditar")
        if not reason.strip():
            raise PurchaseError(
                "PURCHASE_CREDIT_REASON_REQUIRED",
                "Una nota de crédito lleva su motivo",
                hint="Pasa reason con la causa (devolución, corrección del proveedor).",
            )
        [full] = await self.orders.read([order_id], fields=["bill_move_id"])
        reversal_id = await self.accounting.action_reverse(full["bill_move_id"], credit_date)
        await self.orders.write(
            [order_id], {"state": "credited", "credit_note_move_id": reversal_id}
        )
        return reversal_id

    async def action_cancel(self, order_id: int) -> None:
        order = await self._get(order_id)
        if order["state"] == "billed":
            raise PurchaseError(
                "PURCHASE_INVALID_TRANSITION",
                "Una orden con factura registrada no se cancela: revierte su asiento",
                hint="Usa action_credit_note para revertir la factura.",
            )
        if order["state"] == "cancelled":
            return
        await self.orders.write([order_id], {"state": "cancelled"})

    # -- internos ---------------------------------------------------------

    def _expect(self, order: dict[str, Any], state: str, verb: str) -> None:
        if order["state"] != state:
            raise PurchaseError(
                "PURCHASE_INVALID_TRANSITION",
                f"Solo se puede {verb} una orden en estado {state}; esta está en {order['state']}",
            )

    async def _resolve_lines(
        self, order: dict[str, Any]
    ) -> tuple[list[ResolvedLine], dict[str, Any]]:
        result = await self.lines.search(
            [("order_id", "=", order["id"])],
            fields=[
                "id",
                "name",
                "quantity",
                "price_unit",
                "discount_percent",
                "tax_codes",
                "expense_account_id",
            ],
            limit=500,
        )
        rows = sorted(result["rows"], key=lambda row: row["id"])
        if not rows:
            raise PurchaseError("PURCHASE_ORDER_EMPTY", "La orden no tiene líneas que facturar")
        codes = [
            code.strip()
            for row in rows
            for code in (row["tax_codes"] or "").split(",")
            if code.strip()
        ]
        taxes_by_code = await resolve_taxes(
            self.env,
            codes,
            company_id=order["company_id"],
            side="purchase",
            error_prefix="PURCHASE",
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
                account_id=row["expense_account_id"],
            )
            for row in rows
        ]
        return resolved, taxes_by_code

    async def _decimals(self, currency_id: int) -> int:
        currencies = RecordSet(self.env, "res.currency")
        [currency] = await currencies.read([currency_id], fields=["decimal_places"])
        return int(currency["decimal_places"] or 2)

    async def _get(self, order_id: int) -> dict[str, Any]:
        rows = await self.orders.read(
            [order_id],
            fields=[
                "id",
                "name",
                "state",
                "partner_id",
                "date_order",
                "currency_id",
                "journal_id",
                "company_id",
            ],
        )
        if not rows:
            raise PurchaseError("PURCHASE_ORDER_NOT_FOUND", f"No existe la orden {order_id}")
        return rows[0]
