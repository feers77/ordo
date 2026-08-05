"""Del pedido al camión: entregar ventas y recibir compras mueve stock y libros."""

from decimal import Decimal
from typing import Any

import pytest
from modules.purchase.services import PurchaseService
from modules.sale.services import SaleService
from modules.stock.services import StockError, StockService
from ordo_core.actions import dispatch
from ordo_core.recordset import RecordSet
from ordo_core.reports import run_report

pytestmark = pytest.mark.integration


async def confirmed_purchase(shop: dict[str, Any], quantity: str, price: str) -> int:
    service = PurchaseService(shop["env"])
    order_id = await service.create_order(
        partner_id=shop["vendor_id"],
        date_order="2026-08-05",
        currency_id=shop["currency_id"],
        journal_id=shop["purchase_journal"],
        company_id=shop["company_id"],
        lines=[
            {
                "name": "Notebook 14",
                "product_id": shop["product_id"],
                "quantity": quantity,
                "price_unit": Decimal(price),
                "tax_codes": "IVA19C",
            }
        ],
    )
    await service.action_confirm(order_id)
    return order_id


async def confirmed_sale(shop: dict[str, Any], quantity: str) -> int:
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
                "quantity": quantity,
                "price_unit": Decimal("300"),
                "tax_codes": "IVA19",
            },
            {
                "name": "Instalación",
                "product_id": shop["service_id"],
                "quantity": "1",
                "price_unit": Decimal("50"),
                "tax_codes": "IVA19",
            },
        ],
    )
    await service.action_confirm(order_id)
    return order_id


class TestFulfillment:
    async def test_receive_then_deliver_moves_stock_and_books(self, shop: dict[str, Any]) -> None:
        """Compra 10 a 100, vende 4: stock 6, COGS 400 y libros cuadrados."""
        purchase_id = await confirmed_purchase(shop, "10", "100")
        received = await dispatch(shop["env"], "purchase.order", "action_receive", purchase_id, {})
        assert received["name"] == "IN/00001"

        stock = StockService(shop["env"])
        assert await stock.on_hand(shop["product_id"], shop["loc_stock"]) == Decimal("10")

        sale_id = await confirmed_sale(shop, "4")
        delivered = await dispatch(shop["env"], "sale.order", "action_deliver", sale_id, {})
        assert delivered["name"] == "OUT/00001"
        assert delivered["moves"] == 1  # el servicio no se despacha

        assert await stock.on_hand(shop["product_id"], shop["loc_stock"]) == Decimal("6")

        [picking] = await RecordSet(shop["env"], "stock.picking").read(
            [delivered["picking_id"]], fields=["move_id", "origin"]
        )
        assert picking["origin"] == "SO/00001"
        lines = await RecordSet(shop["env"], "account.move.line").search(
            [("move_id", "=", picking["move_id"])], fields=["account_id", "debit"]
        )
        by_account = {row["account_id"]: row for row in lines["rows"]}
        assert by_account[shop["costo_venta"]]["debit"] == Decimal("400.00")

        balance = await run_report(
            shop["env"], "account.trial_balance", {"company_id": shop["company_id"]}
        )
        assert balance["balanced"] is True

    async def test_delivering_without_stock_fails(self, shop: dict[str, Any]) -> None:
        sale_id = await confirmed_sale(shop, "4")
        with pytest.raises(StockError) as excinfo:
            await dispatch(shop["env"], "sale.order", "action_deliver", sale_id, {})
        assert excinfo.value.code == "STOCK_INSUFFICIENT"

    async def test_draft_order_cannot_be_delivered(self, shop: dict[str, Any]) -> None:
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
                }
            ],
        )
        with pytest.raises(StockError) as excinfo:
            await dispatch(shop["env"], "sale.order", "action_deliver", order_id, {})
        assert excinfo.value.code == "STOCK_ORDER_NOT_READY"

    async def test_service_only_order_has_nothing_to_move(self, shop: dict[str, Any]) -> None:
        service = SaleService(shop["env"])
        order_id = await service.create_order(
            partner_id=shop["customer_id"],
            date_order="2026-08-06",
            currency_id=shop["currency_id"],
            journal_id=shop["sale_journal"],
            company_id=shop["company_id"],
            lines=[
                {
                    "name": "Solo instalación",
                    "product_id": shop["service_id"],
                    "quantity": "1",
                    "price_unit": Decimal("50"),
                }
            ],
        )
        await service.action_confirm(order_id)
        with pytest.raises(StockError) as excinfo:
            await dispatch(shop["env"], "sale.order", "action_deliver", order_id, {})
        assert excinfo.value.code == "STOCK_NOTHING_TO_MOVE"


class TestLocationAmbiguity:
    """Con dos bodegas, el sistema exige elegir en vez de adivinar."""

    @staticmethod
    async def second_warehouse(shop: dict[str, Any]) -> tuple[int, int]:
        [warehouse_id] = await RecordSet(shop["env"], "stock.warehouse").create(
            [{"name": "Tienda Providencia", "code": "TP", "company_id": shop["company_id"]}]
        )
        [location_id] = await RecordSet(shop["env"], "stock.location").create(
            [
                {
                    "name": "TP/Sala de ventas",
                    "location_type": "internal",
                    "warehouse_id": warehouse_id,
                    "company_id": shop["company_id"],
                }
            ]
        )
        return warehouse_id, location_id

    async def test_delivering_without_origin_refuses_to_guess(self, shop: dict[str, Any]) -> None:
        purchase_id = await confirmed_purchase(shop, "10", "100")
        await dispatch(
            shop["env"],
            "purchase.order",
            "action_receive",
            purchase_id,
            {"location_to_id": shop["loc_stock"]},
        )
        await self.second_warehouse(shop)

        sale_id = await confirmed_sale(shop, "4")
        with pytest.raises(StockError) as excinfo:
            await dispatch(shop["env"], "sale.order", "action_deliver", sale_id, {})
        assert excinfo.value.code == "STOCK_LOCATION_AMBIGUOUS"
        # el hint nombra las candidatas: el agente puede resolver sin adivinar
        assert excinfo.value.hint is not None
        assert str(shop["loc_stock"]) in excinfo.value.hint

    async def test_explicit_origin_still_delivers(self, shop: dict[str, Any]) -> None:
        purchase_id = await confirmed_purchase(shop, "10", "100")
        await dispatch(
            shop["env"],
            "purchase.order",
            "action_receive",
            purchase_id,
            {"location_to_id": shop["loc_stock"]},
        )
        await self.second_warehouse(shop)

        sale_id = await confirmed_sale(shop, "4")
        delivered = await dispatch(
            shop["env"],
            "sale.order",
            "action_deliver",
            sale_id,
            {"location_from_id": shop["loc_stock"]},
        )
        assert delivered["name"] == "OUT/00001"
        stock = StockService(shop["env"])
        assert await stock.on_hand(shop["product_id"], shop["loc_stock"]) == Decimal("6")

    async def test_warehouse_is_enough_to_disambiguate(self, shop: dict[str, Any]) -> None:
        """Indicar el almacén basta: dentro de él la ubicación interna es única."""
        _, store_location = await self.second_warehouse(shop)

        purchase_id = await confirmed_purchase(shop, "10", "100")
        received = await dispatch(
            shop["env"],
            "purchase.order",
            "action_receive",
            purchase_id,
            {"warehouse_id": shop["warehouse_id"]},
        )
        assert received["name"] == "IN/00001"

        stock = StockService(shop["env"])
        assert await stock.on_hand(shop["product_id"], shop["loc_stock"]) == Decimal("10")
        assert await stock.on_hand(shop["product_id"], store_location) == Decimal("0")

    async def test_receiving_without_destination_refuses_to_guess(
        self, shop: dict[str, Any]
    ) -> None:
        await self.second_warehouse(shop)
        purchase_id = await confirmed_purchase(shop, "10", "100")
        with pytest.raises(StockError) as excinfo:
            await dispatch(shop["env"], "purchase.order", "action_receive", purchase_id, {})
        assert excinfo.value.code == "STOCK_LOCATION_AMBIGUOUS"


class TestReorder:
    async def test_low_stock_raises_an_alert_with_suggestion(self, shop: dict[str, Any]) -> None:
        await RecordSet(shop["env"], "stock.reorder.rule").create(
            [
                {
                    "product_id": shop["product_id"],
                    "location_id": shop["loc_stock"],
                    "min_quantity": "5",
                    "max_quantity": "20",
                    "company_id": shop["company_id"],
                }
            ]
        )
        purchase_id = await confirmed_purchase(shop, "3", "100")
        await dispatch(shop["env"], "purchase.order", "action_receive", purchase_id, {})

        result = await run_report(
            shop["env"], "stock.reorder_alerts", {"company_id": shop["company_id"]}
        )
        [alert] = result["alerts"]
        assert alert["on_hand"] == "3"
        assert alert["suggested_quantity"] == "17"

        # reponer sobre el mínimo apaga la alerta
        second = await confirmed_purchase(shop, "10", "100")
        await dispatch(shop["env"], "purchase.order", "action_receive", second, {})
        result = await run_report(
            shop["env"], "stock.reorder_alerts", {"company_id": shop["company_id"]}
        )
        assert result["count"] == 0
