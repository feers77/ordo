"""El ticket: cobrar y asentar en la misma operación.

`action_validate` fija los totales, asigna el número y contabiliza el asiento
de una vez. No existe el ticket "cobrado" cuyo asiento sigue pendiente: en una
caja que emite doscientos al día, ese estado intermedio es donde se pierde la
plata.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from ordo_core.environment import Environment
from ordo_core.recordset import RecordSet
from ordo_core.services.sequences import SequenceService

from modules.account.invoicing import (
    ResolvedLine,
    build_revenue_lines,
    compute_totals,
    resolve_taxes,
)
from modules.account.services import AccountingService
from modules.pos.cash import ZERO, money, validate_payments
from modules.pos.fulfillment import deliver_ticket, return_ticket
from modules.pos.services import PosError, PosSessionService

SEQUENCE_CODE = "pos.order"


class PosOrderService:
    def __init__(self, env: Environment) -> None:
        self.env = env
        self.orders = RecordSet(env, "pos.order")
        self.lines = RecordSet(env, "pos.order.line")
        self.payments = RecordSet(env, "pos.payment")
        self.methods = RecordSet(env, "pos.payment.method")
        self.sessions = PosSessionService(env)
        self.accounting = AccountingService(env)

    # ---------------------------------------------------------------- lectura

    async def _order(self, order_id: int) -> dict[str, Any]:
        rows = await self.orders.read(
            [order_id],
            fields=[
                "id",
                "name",
                "state",
                "session_id",
                "partner_id",
                "date_order",
                "currency_id",
                "company_id",
            ],
        )
        if not rows:
            raise PosError(
                "POS_ORDER_NOT_FOUND",
                f"No existe el ticket {order_id}",
                hint="Revisa el id contra pos.order.",
            )
        return rows[0]

    async def _open_session(self, session_id: int) -> dict[str, Any]:
        rows = await RecordSet(self.env, "pos.session").read(
            [session_id], fields=["id", "name", "state", "config_id", "company_id"]
        )
        if not rows:
            raise PosError(
                "POS_SESSION_NOT_FOUND",
                f"No existe el turno {session_id}",
                hint="Abre un turno antes de vender.",
            )
        session = rows[0]
        if session["state"] != "opened":
            raise PosError(
                "POS_SESSION_NOT_OPEN",
                f"El turno {session['name'] or session['id']} está en {session['state']}",
                hint="Solo se vende contra un turno abierto; abre uno con action_open.",
            )
        return session

    # -------------------------------------------------------------- escritura

    async def create_order(
        self,
        *,
        session_id: int,
        date_order: str,
        lines: list[dict[str, Any]],
        partner_id: int | None = None,
        terminal_ref: str | None = None,
    ) -> int:
        if not lines:
            raise PosError(
                "POS_ORDER_EMPTY",
                "Un ticket sin líneas no cobra nada",
                hint="Agrega al menos una línea con producto, cantidad y precio.",
            )
        session = await self._open_session(session_id)
        config = await self.sessions.config(session["config_id"])
        if terminal_ref:
            await self._refuse_duplicate_terminal_ref(terminal_ref)

        [order_id] = await self.orders.create(
            [
                {
                    "name": None,
                    "session_id": session_id,
                    "terminal_ref": terminal_ref,
                    "partner_id": partner_id,
                    "state": "draft",
                    "date_order": date_order,
                    "currency_id": config["currency_id"],
                    "amount_untaxed": None,
                    "amount_tax": None,
                    "amount_total": None,
                    "change": None,
                    "move_id": None,
                    "company_id": session["company_id"],
                }
            ]
        )
        await self.lines.create(
            [
                {
                    "order_id": order_id,
                    "name": line["name"],
                    "product_id": line["product_id"],
                    "quantity": str(line.get("quantity", "1")),
                    "price_unit": line["price_unit"],
                    "discount_percent": str(line.get("discount_percent", "0")),
                    "tax_codes": line.get("tax_codes", ""),
                    "income_account_id": line.get("income_account_id"),
                    "company_id": session["company_id"],
                }
                for line in lines
            ]
        )
        return order_id

    async def _refuse_duplicate_terminal_ref(self, terminal_ref: str) -> None:
        """El terminal reintenta tras un corte y la clave de idempotencia se
        pierde con él. `terminal_ref` es la segunda red: el mismo ticket físico
        no se registra dos veces."""
        existing = await self.orders.search(
            [("terminal_ref", "=", terminal_ref)], fields=["id", "name"], limit=1
        )
        if existing["rows"]:
            found = existing["rows"][0]
            raise PosError(
                "POS_DUPLICATE_TERMINAL_REF",
                f"El terminal ya registró ese ticket como {found['name'] or found['id']}",
                hint=f"Recupera el ticket {found['id']} en vez de crear uno nuevo.",
            )

    async def add_payment(self, order_id: int, *, method_id: int, amount: Decimal) -> int:
        order = await self._order(order_id)
        self._expect(order, "draft", "cobrar")
        [payment_id] = await self.payments.create(
            [
                {
                    "order_id": order_id,
                    "method_id": method_id,
                    "amount": money(amount),
                    "company_id": order["company_id"],
                }
            ]
        )
        return payment_id

    async def action_validate(self, order_id: int) -> dict[str, Any]:
        """Cobra el ticket: totales, número y asiento contabilizado, de una vez."""
        order = await self._order(order_id)
        self._expect(order, "draft", "validar")
        session = await self._open_session(order["session_id"])
        config = await self.sessions.config(session["config_id"])

        resolved, taxes_by_code = await self._resolve_lines(order)
        decimals = await self._decimals(order["currency_id"])
        totals = compute_totals(resolved, decimals=decimals)

        payments = await self._payments(order_id)
        change = validate_payments(totals.total_included, payments, decimals=decimals)

        entries, counterpart = build_revenue_lines(
            kind="customer",
            resolved_lines=resolved,
            totals=totals,
            taxes_by_code=taxes_by_code,
            fallback_account_id=await self._fallback_account(config),
            error_prefix="POS",
            decimals=decimals,
        )
        entries = self._settlement_lines(payments, change, decimals) + entries

        sequences = SequenceService(self.env.session)
        await sequences.create(code=SEQUENCE_CODE, name="Tickets de punto de venta", prefix="T/")
        number = await sequences.next_by_code(SEQUENCE_CODE)

        move_id = await self.accounting.create_move(
            journal_id=config["journal_id"],
            move_date=order["date_order"],
            currency_id=order["currency_id"],
            company_id=order["company_id"],
            lines=entries,
            ref=f"Ticket {number}",
            partner_id=order["partner_id"],
        )
        await self.accounting.action_post(move_id)

        await self.orders.write(
            [order_id],
            {
                "state": "paid",
                "name": number,
                "amount_untaxed": totals.base,
                "amount_tax": totals.total_included - totals.base,
                "amount_total": totals.total_included,
                "change": change,
                "move_id": move_id,
            },
        )
        picking_id = await deliver_ticket(self.env, order_id)
        if picking_id is not None:
            await self.orders.write([order_id], {"picking_id": picking_id})
        return {
            "order_id": order_id,
            "name": number,
            "state": "paid",
            "amount_total": str(counterpart),
            "change": str(change),
            "move_id": move_id,
            "picking_id": picking_id,
        }

    async def action_refund(self, order_id: int, *, reason: str) -> dict[str, Any]:
        """Devuelve el ticket completo: documento nuevo, asiento revertido.

        El ticket original **no cambia de estado**. Una devolución es un
        documento por derecho propio, igual que la nota de crédito, y el asiento
        contabilizado no se toca: se revierte (AGENTS.md §2.6).
        """
        original = await self._order(order_id)
        self._expect(original, "paid", "devolver")
        if not reason.strip():
            raise PosError(
                "POS_REFUND_REASON_REQUIRED",
                "Una devolución lleva su motivo",
                hint="Pasa reason con la causa: talla equivocada, falla, arrepentimiento.",
            )
        session = await self._current_session(original["session_id"])
        [full] = await self.orders.read(
            [order_id],
            fields=["move_id", "amount_untaxed", "amount_tax", "amount_total", "refund_of_id"],
        )
        if full["refund_of_id"]:
            raise PosError(
                "POS_ORDER_INVALID_TRANSITION",
                "Una devolución no se devuelve",
                hint="Si la devolución fue un error, emite una venta nueva.",
            )

        lines = await self._mirror_lines(order_id, session)
        [refund_id] = await self.orders.create(
            [
                {
                    "name": None,
                    "session_id": session["id"],
                    "terminal_ref": None,
                    "partner_id": original["partner_id"],
                    "state": "draft",
                    "date_order": original["date_order"],
                    "currency_id": original["currency_id"],
                    "amount_untaxed": -(full["amount_untaxed"] or ZERO),
                    "amount_tax": -(full["amount_tax"] or ZERO),
                    "amount_total": -(full["amount_total"] or ZERO),
                    "change": ZERO,
                    "move_id": None,
                    "picking_id": None,
                    "refund_of_id": order_id,
                    "company_id": original["company_id"],
                }
            ]
        )
        await self.lines.create([{**line, "order_id": refund_id} for line in lines])
        await self._mirror_payments(order_id, refund_id, original["company_id"])

        sequences = SequenceService(self.env.session)
        await sequences.create(code=SEQUENCE_CODE, name="Tickets de punto de venta", prefix="T/")
        number = await sequences.next_by_code(SEQUENCE_CODE)
        reversal_id = await self.accounting.action_reverse(full["move_id"])
        # `action_reverse` deja la reversión en borrador, que es lo correcto
        # para una nota de crédito que alguien puede querer revisar. Aquí no:
        # la plata ya salió del cajón, y un asiento en borrador dejaría los
        # libros atrás de la realidad hasta que alguien se acuerde.
        await self.accounting.action_post(reversal_id)

        await self.orders.write(
            [refund_id], {"state": "paid", "name": number, "move_id": reversal_id}
        )
        picking_id = await return_ticket(self.env, refund_id, order_id)
        if picking_id is not None:
            await self.orders.write([refund_id], {"picking_id": picking_id})
        return {
            "refund_id": refund_id,
            "name": number,
            "state": "paid",
            "refund_of_id": order_id,
            "move_id": reversal_id,
            "picking_id": picking_id,
            "reason": reason,
        }

    async def _current_session(self, original_session_id: int) -> dict[str, Any]:
        """La devolución entra en el turno abierto ahora, no en el de la venta.

        Si entrara en el turno original, un arqueo ya cerrado cambiaría de
        resultado después de haberse firmado.
        """
        [previous] = await RecordSet(self.env, "pos.session").read(
            [original_session_id], fields=["config_id"]
        )
        result = await RecordSet(self.env, "pos.session").search(
            [("config_id", "=", previous["config_id"]), ("state", "=", "opened")],
            fields=["id", "name", "config_id", "company_id"],
            limit=1,
        )
        if not result["rows"]:
            raise PosError(
                "POS_SESSION_NOT_OPEN",
                "No hay un turno abierto en esa caja para registrar la devolución",
                hint="Abre un turno con action_open antes de devolver.",
            )
        return result["rows"][0]

    async def _mirror_lines(self, order_id: int, session: dict[str, Any]) -> list[dict[str, Any]]:
        result = await self.lines.search(
            [("order_id", "=", order_id)],
            fields=[
                "id",
                "name",
                "product_id",
                "quantity",
                "price_unit",
                "discount_percent",
                "tax_codes",
                "income_account_id",
            ],
            limit=200,
        )
        return [
            {
                "name": row["name"],
                "product_id": row["product_id"],
                "quantity": str(-Decimal(row["quantity"] or "1")),
                "price_unit": row["price_unit"],
                "discount_percent": row["discount_percent"],
                "tax_codes": row["tax_codes"],
                "income_account_id": row["income_account_id"],
                "company_id": session["company_id"],
            }
            for row in sorted(result["rows"], key=lambda item: item["id"])
        ]

    async def _mirror_payments(self, order_id: int, refund_id: int, company_id: int) -> None:
        """Los cobros del original, en negativo.

        No los usa la contabilidad —eso lo resuelve la reversión del asiento—
        sino el arqueo: la plata que se devuelve en efectivo sale del cajón y
        el turno tiene que esperar menos al cerrar.
        """
        result = await self.payments.search(
            [("order_id", "=", order_id)], fields=["id", "method_id", "amount"], limit=50
        )
        if not result["rows"]:
            return
        await self.payments.create(
            [
                {
                    "order_id": refund_id,
                    "method_id": row["method_id"],
                    "amount": -Decimal(str(row["amount"])),
                    "company_id": company_id,
                }
                for row in sorted(result["rows"], key=lambda item: item["id"])
            ]
        )

    def _settlement_lines(
        self, payments: list[dict[str, Any]], change: Decimal, decimals: int
    ) -> list[dict[str, Any]]:
        """Una partida al debe por cada cuenta de liquidación.

        Lo que la tienda se queda no es lo cobrado sino lo cobrado menos el
        vuelto, y el vuelto sale del cajón: se descuenta de las cuentas de
        efectivo, no de las de tarjeta. `validate_payments` ya garantizó que el
        efectivo alcanza para darlo.
        """
        by_account: dict[int, Decimal] = {}
        cash_accounts: list[int] = []
        for payment in payments:
            account_id = int(payment["account_id"])
            by_account[account_id] = by_account.get(account_id, ZERO) + Decimal(
                str(payment["amount"])
            )
            if payment["method_type"] == "cash" and account_id not in cash_accounts:
                cash_accounts.append(account_id)

        remaining = change
        for account_id in cash_accounts:
            if remaining <= ZERO:
                break
            taken = min(remaining, by_account[account_id])
            by_account[account_id] -= taken
            remaining -= taken

        return [
            {"account_id": account_id, "name": "Cobro", "debit": money(amount, decimals)}
            for account_id, amount in by_account.items()
            if amount != ZERO
        ]

    async def action_cancel(self, order_id: int) -> dict[str, Any]:
        order = await self._order(order_id)
        if order["state"] == "paid":
            raise PosError(
                "POS_ORDER_INVALID_TRANSITION",
                "Un ticket cobrado no se cancela: emite una devolución",
                hint="La devolución es un documento nuevo, como la nota de crédito.",
            )
        if order["state"] == "cancelled":
            return {"order_id": order_id, "state": "cancelled"}
        await self.orders.write([order_id], {"state": "cancelled"})
        return {"order_id": order_id, "state": "cancelled"}

    # --------------------------------------------------------------- internos

    def _expect(self, order: dict[str, Any], state: str, verb: str) -> None:
        if order["state"] != state:
            raise PosError(
                "POS_ORDER_INVALID_TRANSITION",
                f"Solo se puede {verb} un ticket en {state}; este está en {order['state']}",
                hint="Consulta las acciones disponibles con explain sobre el ticket.",
            )

    async def _payments(self, order_id: int) -> list[dict[str, Any]]:
        result = await self.payments.search(
            [("order_id", "=", order_id)],
            fields=["id", "method_id", "amount"],
            limit=50,
        )
        rows = sorted(result["rows"], key=lambda row: row["id"])
        if not rows:
            raise PosError(
                "POS_PAYMENT_INSUFFICIENT",
                "El ticket no tiene cobros registrados",
                hint="Crea los pos.payment del ticket antes de validarlo.",
            )
        method_ids = sorted({row["method_id"] for row in rows})
        methods = await self.methods.search(
            [("id", "in", method_ids)],
            fields=["id", "code", "method_type", "settlement_account_id"],
            limit=len(method_ids),
        )
        by_id = {row["id"]: row for row in methods["rows"]}
        enriched = []
        for row in rows:
            method = by_id.get(row["method_id"])
            if method is None:
                raise PosError(
                    "POS_PAYMENT_METHOD_UNKNOWN",
                    f"El medio de cobro {row['method_id']} no existe",
                    hint="Usa un pos.payment.method de esta caja.",
                )
            enriched.append(
                {
                    "amount": row["amount"],
                    "method_type": method["method_type"],
                    "account_id": method["settlement_account_id"],
                }
            )
        return enriched

    async def _fallback_account(self, config: dict[str, Any]) -> int | None:
        journals = RecordSet(self.env, "account.journal")
        [journal] = await journals.read([config["journal_id"]], fields=["default_account_id"])
        return journal["default_account_id"]

    async def _resolve_lines(
        self, order: dict[str, Any]
    ) -> tuple[list[ResolvedLine], dict[str, Any]]:
        result = await self.lines.search(
            [("order_id", "=", order["id"])],
            fields=[
                "id",
                "name",
                "product_id",
                "quantity",
                "price_unit",
                "discount_percent",
                "tax_codes",
                "income_account_id",
            ],
            limit=200,
        )
        rows = sorted(result["rows"], key=lambda row: row["id"])
        if not rows:
            raise PosError("POS_ORDER_EMPTY", "El ticket no tiene líneas que cobrar")

        products = await self._product_accounts(rows)
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
            side="sale",
            error_prefix="POS",
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
                account_id=row["income_account_id"] or products.get(row["product_id"]),
            )
            for row in rows
        ]
        return resolved, taxes_by_code

    async def _product_accounts(self, rows: list[dict[str, Any]]) -> dict[int, int | None]:
        product_ids = sorted({row["product_id"] for row in rows if row["product_id"]})
        if not product_ids:
            return {}
        result = await RecordSet(self.env, "product.product").search(
            [("id", "in", product_ids)],
            fields=["id", "income_account_id"],
            limit=len(product_ids),
        )
        return {row["id"]: row["income_account_id"] for row in result["rows"]}

    async def _decimals(self, currency_id: int) -> int:
        [currency] = await RecordSet(self.env, "res.currency").read(
            [currency_id], fields=["decimal_places"]
        )
        return int(currency["decimal_places"] or 2)
