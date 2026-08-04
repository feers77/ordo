"""Cobros y pagos: el dinero se mueve con asiento o no se mueve.

Contabilizar un pago genera su asiento contra el diario de banco o caja y la
cuenta por cobrar o pagar de la compañía. Un pago contabilizado no se edita:
se revierte su asiento, como todo lo demás en este módulo.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from ordo_core.environment import Environment
from ordo_core.recordset import RecordSet

from modules.account.invoicing import settings_for
from modules.account.services import AccountingError, AccountingService

ZERO = Decimal("0")


class PaymentError(AccountingError):
    """Error de pagos con código estable."""


class PaymentService:
    def __init__(self, env: Environment) -> None:
        self.env = env
        self.payments = RecordSet(env, "account.payment")
        self.accounting = AccountingService(env)

    async def create_payment(
        self,
        *,
        payment_type: str,
        partner_id: int,
        amount: Decimal | str,
        date: str,
        journal_id: int,
        currency_id: int,
        company_id: int,
        memo: str | None = None,
    ) -> int:
        value = Decimal(str(amount))
        if value <= ZERO:
            raise PaymentError(
                "PAYMENT_NON_POSITIVE",
                "El importe de un pago debe ser positivo; el sentido lo da payment_type",
                hint="Usa payment_type outbound para dinero que sale.",
            )
        journals = RecordSet(self.env, "account.journal")
        [journal] = await journals.read([journal_id], fields=["journal_type", "default_account_id"])
        if journal["journal_type"] not in ("bank", "cash"):
            raise PaymentError(
                "PAYMENT_JOURNAL_INVALID",
                "Un pago se registra contra un diario de banco o caja",
                hint="Usa un diario con journal_type bank o cash.",
            )
        if journal["default_account_id"] is None:
            raise PaymentError(
                "PAYMENT_JOURNAL_NO_ACCOUNT",
                "El diario del pago no tiene cuenta de banco/caja configurada",
                hint="Fija default_account_id en el diario.",
            )
        [payment_id] = await self.payments.create(
            [
                {
                    "payment_type": payment_type,
                    "partner_id": partner_id,
                    "amount": value,
                    "date": date,
                    "journal_id": journal_id,
                    "currency_id": currency_id,
                    "company_id": company_id,
                    "memo": memo,
                    "state": "draft",
                }
            ]
        )
        return payment_id

    async def action_post(self, payment_id: int) -> int:
        """Contabiliza el pago. Devuelve el id del asiento generado."""
        payment = await self._get(payment_id)
        if payment["state"] != "draft":
            raise PaymentError(
                "PAYMENT_INVALID_TRANSITION",
                f"Solo se contabiliza un pago en borrador; este está en {payment['state']}",
            )
        journals = RecordSet(self.env, "account.journal")
        [journal] = await journals.read([payment["journal_id"]], fields=["default_account_id"])
        settings = await settings_for(self.env, payment["company_id"])

        if payment["payment_type"] == "inbound":
            counterpart = settings["receivable_account_id"]
            missing = "receivable_account_id"
            bank_side, partner_side = "debit", "credit"
        else:
            counterpart = settings["payable_account_id"]
            missing = "payable_account_id"
            bank_side, partner_side = "credit", "debit"
        if counterpart is None:
            raise PaymentError(
                "ACCOUNT_SETTINGS_MISSING",
                f"La configuración contable no define {missing}",
                hint=f"Fija {missing} en account.settings.",
            )

        amount = payment["amount"]
        move_id = await self.accounting.create_move(
            journal_id=payment["journal_id"],
            move_date=payment["date"],
            currency_id=payment["currency_id"],
            company_id=payment["company_id"],
            partner_id=payment["partner_id"],
            ref=payment["memo"] or f"Pago {payment_id}",
            lines=[
                {
                    "account_id": journal["default_account_id"],
                    "name": "Banco/Caja",
                    bank_side: amount,
                },
                {
                    "account_id": counterpart,
                    "name": "Cobro" if payment["payment_type"] == "inbound" else "Pago",
                    "partner_id": payment["partner_id"],
                    partner_side: amount,
                },
            ],
        )
        await self.accounting.action_post(move_id)
        await self.payments.write([payment_id], {"state": "posted", "move_id": move_id})
        return move_id

    async def action_cancel(self, payment_id: int) -> None:
        payment = await self._get(payment_id)
        if payment["state"] == "posted":
            raise PaymentError(
                "PAYMENT_POSTED_IMMUTABLE",
                "Un pago contabilizado no se anula: revierte su asiento",
                hint="Usa action_reverse sobre move_id.",
            )
        await self.payments.write([payment_id], {"state": "cancelled"})

    # -- internos ---------------------------------------------------------

    async def _get(self, payment_id: int) -> dict[str, Any]:
        rows = await self.payments.read(
            [payment_id],
            fields=[
                "id",
                "state",
                "payment_type",
                "partner_id",
                "amount",
                "date",
                "journal_id",
                "currency_id",
                "company_id",
                "memo",
            ],
        )
        if not rows:
            raise PaymentError("PAYMENT_NOT_FOUND", f"No existe el pago {payment_id}")
        return rows[0]
