"""El ticket contra la base real: cobrar, asentar y arquear el turno."""

from decimal import Decimal
from typing import Any

import pytest
from modules.pos.cash import CashError
from modules.pos.order import PosOrderService
from modules.pos.services import PosError
from ordo_core.actions import dispatch
from ordo_core.recordset import RecordSet
from ordo_core.reports import run_report

from tests.integration.pos.conftest import stock_in
from tests.integration.pos.test_session import opened_session

pytestmark = pytest.mark.integration


async def ticket(
    shop: dict[str, Any],
    session_id: int,
    *,
    price: str = "23800",
    quantity: str = "1",
    terminal_ref: str | None = None,
) -> int:
    """Un ticket de una polera con IVA incluido, como lo arma la caja."""
    return await PosOrderService(shop["env"]).create_order(
        session_id=session_id,
        date_order="2026-08-05",
        terminal_ref=terminal_ref,
        lines=[
            {
                "name": "Polera Oversize M / Rojo",
                "product_id": shop["product_id"],
                "quantity": quantity,
                "price_unit": Decimal(price),
                "tax_codes": "IVA19I",
            }
        ],
    )


async def pay(shop: dict[str, Any], order_id: int, *, method: str, amount: str) -> int:
    return await PosOrderService(shop["env"]).add_payment(
        order_id, method_id=shop[method], amount=Decimal(amount)
    )


class TestSellingATicket:
    async def test_cash_ticket_books_and_balances(self, shop: dict[str, Any]) -> None:
        await stock_in(shop, "50", "8000")
        session_id = await opened_session(shop, "50000")
        order_id = await ticket(shop, session_id, price="23800")
        await pay(shop, order_id, method="method_cash", amount="23800")

        result = await dispatch(shop["env"], "pos.order", "action_validate", order_id, {})
        assert result["name"] == "T/00001"
        assert result["change"] == "0"  # CLP no tiene decimales

        [order] = await RecordSet(shop["env"], "pos.order").read(
            [order_id], fields=["state", "amount_untaxed", "amount_tax", "amount_total"]
        )
        assert order["state"] == "paid"
        # IVA incluido: el neto sale del precio, no se suma encima
        assert order["amount_total"] == Decimal("23800.00")
        assert order["amount_untaxed"] == Decimal("20000.00")
        assert order["amount_tax"] == Decimal("3800.00")

        lines = await RecordSet(shop["env"], "account.move.line").search(
            [("move_id", "=", result["move_id"])], fields=["account_id", "debit", "credit"]
        )
        by_account = {row["account_id"]: row for row in lines["rows"]}
        assert by_account[shop["caja"]]["debit"] == Decimal("23800.00")
        assert by_account[shop["ventas"]]["credit"] == Decimal("20000.00")
        assert by_account[shop["iva_debito"]]["credit"] == Decimal("3800.00")

        balance = await run_report(
            shop["env"], "account.trial_balance", {"company_id": shop["company_id"]}
        )
        assert balance["balanced"] is True

    async def test_mixed_payment_lands_in_two_accounts(self, shop: dict[str, Any]) -> None:
        """Efectivo y tarjeta en el mismo ticket: cada uno a su cuenta.

        Es el caso que obligó a partir `build_invoice_lines`: el motor de
        asientos solo sabía poner una contrapartida.
        """
        await stock_in(shop, "50", "8000")
        session_id = await opened_session(shop)
        order_id = await ticket(shop, session_id, price="23800")
        await pay(shop, order_id, method="method_cash", amount="10000")
        await pay(shop, order_id, method="method_card", amount="13800")

        result = await dispatch(shop["env"], "pos.order", "action_validate", order_id, {})
        lines = await RecordSet(shop["env"], "account.move.line").search(
            [("move_id", "=", result["move_id"])], fields=["account_id", "debit", "credit"]
        )
        by_account = {row["account_id"]: row for row in lines["rows"]}
        assert by_account[shop["caja"]]["debit"] == Decimal("10000.00")
        assert by_account[shop["tarjetas"]]["debit"] == Decimal("13800.00")

        balance = await run_report(
            shop["env"], "account.trial_balance", {"company_id": shop["company_id"]}
        )
        assert balance["balanced"] is True

    async def test_change_leaves_only_what_the_shop_keeps(self, shop: dict[str, Any]) -> None:
        """Se paga con $30.000 un ticket de $23.800: a caja entran $23.800,
        no $30.000. El vuelto sale del cajón."""
        await stock_in(shop, "50", "8000")
        session_id = await opened_session(shop)
        order_id = await ticket(shop, session_id, price="23800")
        await pay(shop, order_id, method="method_cash", amount="30000")

        result = await dispatch(shop["env"], "pos.order", "action_validate", order_id, {})
        assert result["change"] == "6200"

        lines = await RecordSet(shop["env"], "account.move.line").search(
            [("move_id", "=", result["move_id"])], fields=["account_id", "debit"]
        )
        by_account = {row["account_id"]: row for row in lines["rows"]}
        assert by_account[shop["caja"]]["debit"] == Decimal("23800.00")

        balance = await run_report(
            shop["env"], "account.trial_balance", {"company_id": shop["company_id"]}
        )
        assert balance["balanced"] is True

    async def test_change_never_comes_out_of_the_card(self, shop: dict[str, Any]) -> None:
        await stock_in(shop, "50", "8000")
        session_id = await opened_session(shop)
        order_id = await ticket(shop, session_id, price="10000")
        await pay(shop, order_id, method="method_card", amount="15000")
        with pytest.raises(CashError) as excinfo:
            await dispatch(shop["env"], "pos.order", "action_validate", order_id, {})
        assert excinfo.value.code == "POS_CHANGE_ON_NON_CASH"

    async def test_underpaying_is_refused(self, shop: dict[str, Any]) -> None:
        await stock_in(shop, "50", "8000")
        session_id = await opened_session(shop)
        order_id = await ticket(shop, session_id, price="23800")
        await pay(shop, order_id, method="method_cash", amount="10000")
        with pytest.raises(CashError) as excinfo:
            await dispatch(shop["env"], "pos.order", "action_validate", order_id, {})
        assert excinfo.value.code == "POS_PAYMENT_INSUFFICIENT"


class TestTicketRefusals:
    async def test_a_ticket_needs_an_open_shift(self, shop: dict[str, Any]) -> None:
        session_id = await opened_session(shop)
        await dispatch(shop["env"], "pos.session", "action_close_register", session_id, {})
        with pytest.raises(PosError) as excinfo:
            await ticket(shop, session_id)
        assert excinfo.value.code == "POS_SESSION_NOT_OPEN"

    async def test_an_empty_ticket_charges_nothing(self, shop: dict[str, Any]) -> None:
        session_id = await opened_session(shop)
        with pytest.raises(PosError) as excinfo:
            await PosOrderService(shop["env"]).create_order(
                session_id=session_id, date_order="2026-08-05", lines=[]
            )
        assert excinfo.value.code == "POS_ORDER_EMPTY"

    async def test_the_terminal_cannot_register_the_same_ticket_twice(
        self, shop: dict[str, Any]
    ) -> None:
        """La clave de idempotencia se pierde con el corte; terminal_ref no."""
        session_id = await opened_session(shop)
        await ticket(shop, session_id, terminal_ref="CAJA1-20260805-0042")
        with pytest.raises(PosError) as excinfo:
            await ticket(shop, session_id, terminal_ref="CAJA1-20260805-0042")
        assert excinfo.value.code == "POS_DUPLICATE_TERMINAL_REF"

    async def test_a_paid_ticket_is_not_cancelled(self, shop: dict[str, Any]) -> None:
        await stock_in(shop, "50", "8000")
        session_id = await opened_session(shop)
        order_id = await ticket(shop, session_id, price="23800")
        await pay(shop, order_id, method="method_cash", amount="23800")
        await dispatch(shop["env"], "pos.order", "action_validate", order_id, {})
        with pytest.raises(PosError) as excinfo:
            await dispatch(shop["env"], "pos.order", "action_cancel", order_id, {})
        assert excinfo.value.code == "POS_ORDER_INVALID_TRANSITION"

    async def test_validating_twice_does_not_book_twice(self, shop: dict[str, Any]) -> None:
        await stock_in(shop, "50", "8000")
        session_id = await opened_session(shop)
        order_id = await ticket(shop, session_id, price="23800")
        await pay(shop, order_id, method="method_cash", amount="23800")
        await dispatch(shop["env"], "pos.order", "action_validate", order_id, {})
        with pytest.raises(PosError) as excinfo:
            await dispatch(shop["env"], "pos.order", "action_validate", order_id, {})
        assert excinfo.value.code == "POS_ORDER_INVALID_TRANSITION"

    async def test_dry_run_does_not_burn_the_ticket_number(self, shop: dict[str, Any]) -> None:
        await stock_in(shop, "50", "8000")
        session_id = await opened_session(shop)
        order_id = await ticket(shop, session_id, price="23800")
        await pay(shop, order_id, method="method_cash", amount="23800")
        simulated = await dispatch(
            shop["env"], "pos.order", "action_validate", order_id, {}, dry_run=True
        )
        assert simulated["would_return"]["name"] == "T/00001"

        [order] = await RecordSet(shop["env"], "pos.order").read(
            [order_id], fields=["state", "name"]
        )
        assert order["state"] == "draft"
        assert order["name"] is None

        real = await dispatch(shop["env"], "pos.order", "action_validate", order_id, {})
        assert real["name"] == "T/00001"


class TestShiftWithTickets:
    async def test_the_audit_counts_cash_but_not_cards(self, shop: dict[str, Any]) -> None:
        """La tarjeta no pasa por el cajón: contarla haría aparecer un faltante
        que no existe."""
        await stock_in(shop, "50", "8000")
        session_id = await opened_session(shop, "50000")

        cash_order = await ticket(shop, session_id, price="23800")
        await pay(shop, cash_order, method="method_cash", amount="30000")
        await dispatch(shop["env"], "pos.order", "action_validate", cash_order, {})

        card_order = await ticket(shop, session_id, price="10000")
        await pay(shop, card_order, method="method_card", amount="10000")
        await dispatch(shop["env"], "pos.order", "action_validate", card_order, {})

        await dispatch(shop["env"], "pos.session", "action_close_register", session_id, {})
        result = await dispatch(
            shop["env"],
            "pos.session",
            "action_close",
            session_id,
            {"counted_cash": "73800"},
        )
        # 50.000 de fondo + 30.000 recibidos - 6.200 de vuelto = 73.800
        assert result["expected_cash"] == "73800.00"
        assert result["difference"] == "0.00"
        assert result["move_id"] is None

        balance = await run_report(
            shop["env"], "account.trial_balance", {"company_id": shop["company_id"]}
        )
        assert balance["balanced"] is True

    async def test_a_shift_with_uncharged_tickets_cannot_close(self, shop: dict[str, Any]) -> None:
        """Cerrar dejándolos vivos los deja huérfanos: ni se pueden cobrar
        después ni entraron en el arqueo."""
        session_id = await opened_session(shop)
        await ticket(shop, session_id)
        with pytest.raises(PosError) as excinfo:
            await dispatch(shop["env"], "pos.session", "action_close_register", session_id, {})
        assert excinfo.value.code == "POS_SESSION_HAS_DRAFT_ORDERS"

    async def test_cancelling_the_pending_ticket_unblocks_the_close(
        self, shop: dict[str, Any]
    ) -> None:
        await stock_in(shop, "50", "8000")
        session_id = await opened_session(shop, "50000")
        order_id = await ticket(shop, session_id)
        await dispatch(shop["env"], "pos.order", "action_cancel", order_id, {})
        await dispatch(shop["env"], "pos.session", "action_close_register", session_id, {})
        result = await dispatch(
            shop["env"],
            "pos.session",
            "action_close",
            session_id,
            {"counted_cash": "50000"},
        )
        assert result["difference"] == "0.00"

    async def test_selling_does_not_need_approval(self, shop: dict[str, Any]) -> None:
        """ADR-019: el límite por venta lo pone el capability token, no el
        metadato de la acción. Pedir permiso por cada polera mataría la caja."""
        from ordo_core.actions import actions_for

        by_name = {spec.name: spec for spec in actions_for("pos.order")}
        assert by_name["action_validate"].requires_approval is False


class TestAccountingRefactorIsBehaviourPreserving:
    async def test_a_sale_order_still_books_a_single_counterpart(
        self, shop: dict[str, Any]
    ) -> None:
        """`build_invoice_lines` se partió para admitir cobros mixtos; la
        factura de siempre debe seguir con una sola contrapartida."""
        from modules.sale.services import SaleService

        service = SaleService(shop["env"])
        order_id = await service.create_order(
            partner_id=shop["customer_id"],
            date_order="2026-08-06",
            currency_id=shop["currency_id"],
            journal_id=shop["sale_journal"],
            company_id=shop["company_id"],
            lines=[
                {
                    "name": "Notebook 14",
                    "product_id": shop["product_id"],
                    "quantity": "1",
                    "price_unit": Decimal("300"),
                    "tax_codes": "IVA19",
                }
            ],
        )
        await service.action_confirm(order_id)
        move_id = await service.action_invoice(order_id)
        lines = await RecordSet(shop["env"], "account.move.line").search(
            [("move_id", "=", move_id)], fields=["account_id", "debit", "credit"]
        )
        debits = [row for row in lines["rows"] if row["debit"] != Decimal("0")]
        assert len(debits) == 1
        assert debits[0]["account_id"] == shop["clientes"]
        assert debits[0]["debit"] == Decimal("357.00")
