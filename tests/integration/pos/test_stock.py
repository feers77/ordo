"""El ticket mueve stock y la devolución lo devuelve al costo con que salió."""

from decimal import Decimal
from typing import Any

import pytest
from modules.pos.services import PosError
from modules.stock.services import StockError, StockService
from ordo_core.actions import dispatch
from ordo_core.recordset import RecordSet
from ordo_core.reports import run_report

from tests.integration.pos.conftest import stock_in
from tests.integration.pos.test_order import pay, ticket
from tests.integration.pos.test_session import opened_session

pytestmark = pytest.mark.integration


async def sell(shop: dict[str, Any], session_id: int, *, price: str = "23800") -> int:
    order_id = await ticket(shop, session_id, price=price)
    await pay(shop, order_id, method="method_cash", amount=price)
    await dispatch(shop["env"], "pos.order", "action_validate", order_id, {})
    return order_id


class TestTicketMovesStock:
    async def test_selling_leaves_the_shop_floor(self, shop: dict[str, Any]) -> None:
        await stock_in(shop, "10", "8000")
        session_id = await opened_session(shop, "50000")
        order_id = await sell(shop, session_id)

        service = StockService(shop["env"])
        assert await service.on_hand(shop["product_id"], shop["loc_store"]) == Decimal("9")

        [order] = await RecordSet(shop["env"], "pos.order").read([order_id], fields=["picking_id"])
        assert order["picking_id"] is not None

        balance = await run_report(
            shop["env"], "account.trial_balance", {"company_id": shop["company_id"]}
        )
        assert balance["balanced"] is True

    async def test_the_ticket_takes_from_the_shop_not_the_warehouse(
        self, shop: dict[str, Any]
    ) -> None:
        """La caja despacha de su sala de ventas. Con dos ubicaciones internas
        vivas, tomar de la bodega central sería el bug que arregló PR-A0."""
        await stock_in(shop, "10", "8000")
        service = StockService(shop["env"])
        warehouse_picking = await service.create_picking(
            picking_type="in",
            date="2026-08-04",
            company_id=shop["company_id"],
            partner_id=shop["vendor_id"],
            origin="Carga bodega",
            moves=[
                {
                    "product_id": shop["product_id"],
                    "quantity": "20",
                    "location_from_id": shop["loc_supplier"],
                    "location_to_id": shop["loc_stock"],
                    "price_unit": Decimal("8000"),
                }
            ],
        )
        await service.action_validate(warehouse_picking)

        session_id = await opened_session(shop)
        await sell(shop, session_id)

        assert await service.on_hand(shop["product_id"], shop["loc_store"]) == Decimal("9")
        assert await service.on_hand(shop["product_id"], shop["loc_stock"]) == Decimal("20")

    async def test_selling_without_stock_refuses(self, shop: dict[str, Any]) -> None:
        session_id = await opened_session(shop)
        order_id = await ticket(shop, session_id, price="23800")
        await pay(shop, order_id, method="method_cash", amount="23800")
        with pytest.raises(StockError) as excinfo:
            await dispatch(shop["env"], "pos.order", "action_validate", order_id, {})
        assert excinfo.value.code == "STOCK_INSUFFICIENT"


class TestRefund:
    async def test_refund_reverses_the_entry_and_returns_the_goods(
        self, shop: dict[str, Any]
    ) -> None:
        await stock_in(shop, "10", "8000")
        session_id = await opened_session(shop, "50000")
        order_id = await sell(shop, session_id)

        result = await dispatch(
            shop["env"],
            "pos.order",
            "action_refund",
            order_id,
            {"reason": "talla equivocada"},
        )
        assert result["refund_of_id"] == order_id
        assert result["name"] == "T/00002"

        service = StockService(shop["env"])
        assert await service.on_hand(shop["product_id"], shop["loc_store"]) == Decimal("10")

        # el ticket original no cambia de estado: la devolución es otro documento
        [original] = await RecordSet(shop["env"], "pos.order").read([order_id], fields=["state"])
        assert original["state"] == "paid"

        [reversal] = await RecordSet(shop["env"], "account.move").read(
            [result["move_id"]], fields=["state"]
        )
        assert reversal["state"] == "posted"

        balance = await run_report(
            shop["env"], "account.trial_balance", {"company_id": shop["company_id"]}
        )
        assert balance["balanced"] is True

    async def test_the_return_enters_at_the_cost_it_left_with(self, shop: dict[str, Any]) -> None:
        """Si entre la venta y la devolución llegó un lote más caro, valorizar
        la devolución al promedio nuevo infla el inventario y regala margen."""
        await stock_in(shop, "10", "8000")
        session_id = await opened_session(shop, "50000")
        order_id = await sell(shop, session_id)

        # llega un lote mucho más caro: el promedio sube
        await stock_in(shop, "10", "20000")
        [product] = await RecordSet(shop["env"], "product.product").read(
            [shop["product_id"]], fields=["cost"]
        )
        assert product["cost"] > Decimal("8000")

        result = await dispatch(
            shop["env"], "pos.order", "action_refund", order_id, {"reason": "falla"}
        )
        moves = await RecordSet(shop["env"], "stock.move").search(
            [("picking_id", "=", result["picking_id"])], fields=["id", "price_unit"]
        )
        assert moves["rows"][0]["price_unit"] == Decimal("8000.00")

        balance = await run_report(
            shop["env"], "account.trial_balance", {"company_id": shop["company_id"]}
        )
        assert balance["balanced"] is True

    async def test_the_return_credits_cost_of_sales_not_a_supplier(
        self, shop: dict[str, Any]
    ) -> None:
        """Acreditar recepciones por facturar diría que le debemos la
        mercadería a un proveedor, y es falso: vuelve de un cliente."""
        await stock_in(shop, "10", "8000")
        session_id = await opened_session(shop, "50000")
        order_id = await sell(shop, session_id)
        result = await dispatch(
            shop["env"], "pos.order", "action_refund", order_id, {"reason": "falla"}
        )
        [picking] = await RecordSet(shop["env"], "stock.picking").read(
            [result["picking_id"]], fields=["move_id"]
        )
        lines = await RecordSet(shop["env"], "account.move.line").search(
            [("move_id", "=", picking["move_id"])], fields=["account_id", "debit", "credit"]
        )
        by_account = {row["account_id"]: row for row in lines["rows"]}
        assert by_account[shop["inventario"]]["debit"] == Decimal("8000.00")
        assert by_account[shop["costo_venta"]]["credit"] == Decimal("8000.00")
        assert shop["recepciones"] not in by_account

    async def test_the_refund_lands_in_the_open_shift_not_the_old_one(
        self, shop: dict[str, Any]
    ) -> None:
        """Si entrara en el turno original, un arqueo ya firmado cambiaría de
        resultado después."""
        await stock_in(shop, "10", "8000")
        first = await opened_session(shop, "50000")
        order_id = await sell(shop, first)
        await dispatch(shop["env"], "pos.session", "action_close_register", first, {})
        await dispatch(shop["env"], "pos.session", "action_close", first, {"counted_cash": "73800"})

        second = await opened_session(shop, "50000")
        result = await dispatch(
            shop["env"], "pos.order", "action_refund", order_id, {"reason": "cambio de opinion"}
        )
        [refund] = await RecordSet(shop["env"], "pos.order").read(
            [result["refund_id"]], fields=["session_id"]
        )
        assert refund["session_id"] == second

        # y el arqueo del turno nuevo espera menos efectivo: salió del cajón
        await dispatch(shop["env"], "pos.session", "action_close_register", second, {})
        closed = await dispatch(
            shop["env"],
            "pos.session",
            "action_close",
            second,
            {"counted_cash": "26200"},
        )
        # 50.000 de fondo menos los 23.800 que salieron del cajón
        assert closed["expected_cash"] == "26200.00"
        assert closed["difference"] == "0.00"

    async def test_a_refund_needs_a_reason(self, shop: dict[str, Any]) -> None:
        await stock_in(shop, "10", "8000")
        session_id = await opened_session(shop)
        order_id = await sell(shop, session_id)
        with pytest.raises(PosError) as excinfo:
            await dispatch(shop["env"], "pos.order", "action_refund", order_id, {"reason": " "})
        assert excinfo.value.code == "POS_REFUND_REASON_REQUIRED"

    async def test_a_refund_is_not_refunded(self, shop: dict[str, Any]) -> None:
        await stock_in(shop, "10", "8000")
        session_id = await opened_session(shop)
        order_id = await sell(shop, session_id)
        result = await dispatch(
            shop["env"], "pos.order", "action_refund", order_id, {"reason": "falla"}
        )
        with pytest.raises(PosError) as excinfo:
            await dispatch(
                shop["env"], "pos.order", "action_refund", result["refund_id"], {"reason": "x"}
            )
        assert excinfo.value.code == "POS_ORDER_INVALID_TRANSITION"

    async def test_refunding_needs_approval(self, shop: dict[str, Any]) -> None:
        """Devolver es sacar plata del cajón; es lo que un cajero no debería
        poder hacer solo."""
        from ordo_core.actions import actions_for

        by_name = {spec.name: spec for spec in actions_for("pos.order")}
        assert by_name["action_refund"].requires_approval is True


class TestReports:
    async def test_the_z_report_nets_sales_and_refunds(self, shop: dict[str, Any]) -> None:
        await stock_in(shop, "10", "8000")
        session_id = await opened_session(shop, "50000")
        await sell(shop, session_id, price="23800")
        second = await sell(shop, session_id, price="11900")
        await dispatch(shop["env"], "pos.order", "action_refund", second, {"reason": "talla"})

        summary = await run_report(shop["env"], "pos.session_summary", {"session_id": session_id})
        assert summary["tickets"] == 2
        assert summary["refunds"] == 1
        assert summary["net_total"] == "23800.00"
        # el cobro de la devolución viene en negativo: el medio queda neto
        assert summary["by_method"]["EFECTIVO"] == "23800.00"

    async def test_cash_differences_lists_the_closed_shifts(self, shop: dict[str, Any]) -> None:
        session_id = await opened_session(shop, "50000")
        await dispatch(shop["env"], "pos.session", "action_close_register", session_id, {})
        await dispatch(
            shop["env"],
            "pos.session",
            "action_close",
            session_id,
            {"counted_cash": "49500", "note": "faltan quinientos"},
        )
        report = await run_report(
            shop["env"], "pos.cash_differences", {"company_id": shop["company_id"]}
        )
        assert report["sessions"] == 1
        assert report["total_shortfalls"] == "-500.00"
        assert report["rows"][0]["register"] == "Caja 1"
        assert report["rows"][0]["note"] == "faltan quinientos"
