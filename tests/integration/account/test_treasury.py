"""Tesorería contra base real: pagos, conciliación, extractos y reportes."""

from decimal import Decimal
from typing import Any

import pytest
from modules.account.payments import PaymentError, PaymentService
from modules.account.reconcile import ReconcileError, ReconcileService, open_items
from modules.account.statements import StatementError, StatementService
from modules.sale.services import SaleService
from ordo_core.recordset import RecordSet

pytestmark = pytest.mark.integration


async def invoiced_sale(shop: dict[str, Any], amount: str = "100000") -> tuple[int, int]:
    """Orden facturada; devuelve (order_id, move_id)."""
    service = SaleService(shop["env"])
    order_id = await service.create_order(
        partner_id=shop["customer_id"],
        date_order="2026-08-04",
        currency_id=shop["currency_id"],
        journal_id=shop["sale_journal"],
        company_id=shop["company_id"],
        lines=[
            {
                "name": "Licencia anual",
                "quantity": "1",
                "price_unit": Decimal(amount),
                "tax_codes": "IVA19",
            }
        ],
    )
    await service.action_confirm(order_id)
    move_id = await service.action_invoice(order_id)
    return order_id, move_id


async def posted_payment(shop: dict[str, Any], amount: str = "119000") -> tuple[int, int]:
    """Cobro contabilizado; devuelve (payment_id, move_id)."""
    service = PaymentService(shop["env"])
    payment_id = await service.create_payment(
        payment_type="inbound",
        partner_id=shop["customer_id"],
        amount=Decimal(amount),
        date="2026-08-10",
        journal_id=shop["bank_journal"],
        currency_id=shop["currency_id"],
        company_id=shop["company_id"],
        memo="Transferencia",
    )
    move_id = await service.action_post(payment_id)
    return payment_id, move_id


async def receivable_lines(shop: dict[str, Any]) -> list[dict[str, Any]]:
    return await open_items(
        shop["env"], account_id=shop["clientes"], partner_id=shop["customer_id"]
    )


class TestPayments:
    async def test_inbound_payment_posts_bank_against_receivable(
        self, shop: dict[str, Any]
    ) -> None:
        _, move_id = await posted_payment(shop)
        lines = RecordSet(shop["env"], "account.move.line")
        result = await lines.search(
            [("move_id", "=", move_id)], fields=["account_id", "debit", "credit"]
        )
        by_account = {row["account_id"]: row for row in result["rows"]}
        assert by_account[shop["banco"]]["debit"] == Decimal("119000")
        assert by_account[shop["clientes"]]["credit"] == Decimal("119000")

    async def test_non_positive_amount_is_rejected(self, shop: dict[str, Any]) -> None:
        with pytest.raises(PaymentError) as excinfo:
            await PaymentService(shop["env"]).create_payment(
                payment_type="inbound",
                partner_id=shop["customer_id"],
                amount=Decimal("0"),
                date="2026-08-10",
                journal_id=shop["bank_journal"],
                currency_id=shop["currency_id"],
                company_id=shop["company_id"],
            )
        assert excinfo.value.code == "PAYMENT_NON_POSITIVE"

    async def test_sale_journal_is_not_a_payment_journal(self, shop: dict[str, Any]) -> None:
        with pytest.raises(PaymentError) as excinfo:
            await PaymentService(shop["env"]).create_payment(
                payment_type="inbound",
                partner_id=shop["customer_id"],
                amount=Decimal("1000"),
                date="2026-08-10",
                journal_id=shop["sale_journal"],
                currency_id=shop["currency_id"],
                company_id=shop["company_id"],
            )
        assert excinfo.value.code == "PAYMENT_JOURNAL_INVALID"

    async def test_posted_payment_cannot_be_cancelled(self, shop: dict[str, Any]) -> None:
        payment_id, _ = await posted_payment(shop)
        with pytest.raises(PaymentError) as excinfo:
            await PaymentService(shop["env"]).action_cancel(payment_id)
        assert excinfo.value.code == "PAYMENT_POSTED_IMMUTABLE"


class TestReconciliation:
    async def test_invoice_and_payment_settle_the_receivable(self, shop: dict[str, Any]) -> None:
        """Factura por cobrar + cobro = cuenta saldada y sin partidas abiertas."""
        await invoiced_sale(shop)
        await posted_payment(shop)

        open_before = await receivable_lines(shop)
        assert len(open_before) == 2  # debe de la factura, haber del cobro

        service = ReconcileService(shop["env"])
        group_id = await service.reconcile([row["id"] for row in open_before])
        assert group_id > 0
        assert await receivable_lines(shop) == []

    async def test_unbalanced_group_is_rejected(self, shop: dict[str, Any]) -> None:
        await invoiced_sale(shop)
        await posted_payment(shop, amount="100000")  # cobro parcial
        rows = await receivable_lines(shop)
        with pytest.raises(ReconcileError) as excinfo:
            await ReconcileService(shop["env"]).reconcile([row["id"] for row in rows])
        assert excinfo.value.code == "RECONCILE_UNBALANCED"

    async def test_non_reconcilable_account_is_rejected(self, shop: dict[str, Any]) -> None:
        _, move_id = await invoiced_sale(shop)
        lines = RecordSet(shop["env"], "account.move.line")
        result = await lines.search(
            [("move_id", "=", move_id), ("account_id", "=", shop["ventas"])], fields=["id"]
        )
        with pytest.raises(ReconcileError) as excinfo:
            await ReconcileService(shop["env"]).reconcile(
                [result["rows"][0]["id"], result["rows"][0]["id"] + 1]
            )
        assert excinfo.value.code in {
            "RECONCILE_ACCOUNT_NOT_RECONCILABLE",
            "RECONCILE_MIXED_ACCOUNTS",
        }

    async def test_unreconcile_releases_the_lines(self, shop: dict[str, Any]) -> None:
        await invoiced_sale(shop)
        await posted_payment(shop)
        rows = await receivable_lines(shop)
        service = ReconcileService(shop["env"])
        group_id = await service.reconcile([row["id"] for row in rows])

        released = await service.unreconcile(group_id)
        assert released == 2
        assert len(await receivable_lines(shop)) == 2


class TestStatements:
    async def make_statement(
        self, shop: dict[str, Any], *, amounts: list[str], balance_end: str
    ) -> int:
        return await StatementService(shop["env"]).create_statement(
            name="BCO 2026-08",
            journal_id=shop["bank_journal"],
            date="2026-08-31",
            balance_start=Decimal("0"),
            balance_end=Decimal(balance_end),
            company_id=shop["company_id"],
            lines=[
                {"date": "2026-08-10", "amount": Decimal(amount), "ref": f"MOV {index}"}
                for index, amount in enumerate(amounts, start=1)
            ],
        )

    async def test_auto_match_finds_the_unique_payment(self, shop: dict[str, Any]) -> None:
        await invoiced_sale(shop)
        await posted_payment(shop)
        statement_id = await self.make_statement(shop, amounts=["119000"], balance_end="119000")
        service = StatementService(shop["env"])
        outcome = await service.auto_match(statement_id)
        assert outcome == {"matched": 1, "unmatched": 0}

        await service.action_validate(statement_id)
        statements = RecordSet(shop["env"], "account.bank.statement")
        [statement] = await statements.read([statement_id], fields=["state"])
        assert statement["state"] == "validated"

    async def test_ambiguous_amounts_stay_unmatched(self, shop: dict[str, Any]) -> None:
        """Dos pagos idénticos: el emparejador no adivina."""
        await posted_payment(shop, amount="50000")
        await posted_payment(shop, amount="50000")
        statement_id = await self.make_statement(shop, amounts=["50000"], balance_end="50000")
        outcome = await StatementService(shop["env"]).auto_match(statement_id)
        assert outcome == {"matched": 0, "unmatched": 1}

    async def test_unbalanced_statement_does_not_validate(self, shop: dict[str, Any]) -> None:
        await posted_payment(shop)
        statement_id = await self.make_statement(shop, amounts=["119000"], balance_end="999999")
        service = StatementService(shop["env"])
        await service.auto_match(statement_id)
        with pytest.raises(StatementError) as excinfo:
            await service.action_validate(statement_id)
        assert excinfo.value.code == "STATEMENT_UNBALANCED"

    async def test_unmatched_lines_block_validation(self, shop: dict[str, Any]) -> None:
        statement_id = await self.make_statement(shop, amounts=["777"], balance_end="777")
        with pytest.raises(StatementError) as excinfo:
            await StatementService(shop["env"]).action_validate(statement_id)
        assert excinfo.value.code == "STATEMENT_UNMATCHED"


class TestReports:
    async def test_trial_balance_balances_after_the_full_cycle(self, shop: dict[str, Any]) -> None:
        from ordo_core.reports import run_report

        await invoiced_sale(shop)
        await posted_payment(shop)
        result = await run_report(
            shop["env"], "account.trial_balance", {"company_id": shop["company_id"]}
        )
        assert result["balanced"] is True
        assert result["total_debit"] == result["total_credit"]
        codes = {row["code"] for row in result["rows"]}
        assert {"1101", "1201", "2105", "4101"} <= codes

    async def test_income_statement_shows_the_sale(self, shop: dict[str, Any]) -> None:
        from ordo_core.reports import run_report

        await invoiced_sale(shop)
        result = await run_report(
            shop["env"], "account.income_statement", {"company_id": shop["company_id"]}
        )
        assert Decimal(result["total_income"]) == Decimal("100000")
        assert Decimal(result["result"]) == Decimal("100000")

    async def test_balance_sheet_closes_with_the_period_result(self, shop: dict[str, Any]) -> None:
        from ordo_core.reports import run_report

        await invoiced_sale(shop)
        await posted_payment(shop)
        result = await run_report(
            shop["env"], "account.balance_sheet", {"company_id": shop["company_id"]}
        )
        assert result["balanced"] is True
        assert Decimal(result["period_result"]) == Decimal("100000")

    async def test_missing_company_is_a_stable_error(self, shop: dict[str, Any]) -> None:
        from ordo_core.errors import KernelError
        from ordo_core.reports import run_report

        with pytest.raises(KernelError) as excinfo:
            await run_report(shop["env"], "account.trial_balance", {})
        assert excinfo.value.code == "REPORT_PARAM_REQUIRED"
