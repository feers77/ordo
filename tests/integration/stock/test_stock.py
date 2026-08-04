"""Inventario contra base real: mover, valorizar y asentar son una sola cosa."""

from decimal import Decimal
from typing import Any

import pytest
from modules.stock.services import StockError, StockService
from ordo_core.recordset import RecordSet
from ordo_core.reports import run_report

pytestmark = pytest.mark.integration


def service(shop: dict[str, Any]) -> StockService:
    return StockService(shop["env"])


async def receive(shop: dict[str, Any], quantity: str, price: str, *, validate: bool = True) -> int:
    stock = service(shop)
    picking_id = await stock.create_picking(
        picking_type="in",
        date="2026-08-05",
        company_id=shop["company_id"],
        partner_id=shop["vendor_id"],
        moves=[
            {
                "product_id": shop["product_id"],
                "quantity": quantity,
                "location_from_id": shop["loc_supplier"],
                "location_to_id": shop["loc_stock"],
                "price_unit": Decimal(price),
            }
        ],
    )
    if validate:
        await stock.action_validate(picking_id)
    return picking_id


async def deliver(shop: dict[str, Any], quantity: str) -> int:
    stock = service(shop)
    picking_id = await stock.create_picking(
        picking_type="out",
        date="2026-08-06",
        company_id=shop["company_id"],
        partner_id=shop["customer_id"],
        moves=[
            {
                "product_id": shop["product_id"],
                "quantity": quantity,
                "location_from_id": shop["loc_stock"],
                "location_to_id": shop["loc_customer"],
            }
        ],
    )
    await stock.action_validate(picking_id)
    return picking_id


async def product_cost(shop: dict[str, Any]) -> Decimal:
    [product] = await RecordSet(shop["env"], "product.product").read(
        [shop["product_id"]], fields=["cost"]
    )
    return product["cost"] or Decimal("0")


async def layers_value(shop: dict[str, Any]) -> Decimal:
    result = await RecordSet(shop["env"], "stock.valuation.layer").search(
        [("product_id", "=", shop["product_id"])], fields=["value"], limit=500
    )
    return sum((row["value"] for row in result["rows"]), Decimal("0"))


class TestValuationFlow:
    async def test_receipt_moves_costs_and_posts(self, shop: dict[str, Any]) -> None:
        """Recibir 10 a 100: stock 10, costo 100, capa +1000 y asiento posteado."""
        picking_id = await receive(shop, "10", "100")
        stock = service(shop)

        assert await stock.on_hand(shop["product_id"], shop["loc_stock"]) == Decimal("10")
        assert await product_cost(shop) == Decimal("100.00")
        assert await layers_value(shop) == Decimal("1000.00")

        [picking] = await RecordSet(shop["env"], "stock.picking").read(
            [picking_id], fields=["name", "state", "move_id"]
        )
        assert picking["state"] == "done"
        assert picking["name"] == "IN/00001"
        [move] = await RecordSet(shop["env"], "account.move").read(
            [picking["move_id"]], fields=["state"]
        )
        assert move["state"] == "posted"

        lines = await RecordSet(shop["env"], "account.move.line").search(
            [("move_id", "=", picking["move_id"])], fields=["account_id", "debit", "credit"]
        )
        by_account = {row["account_id"]: row for row in lines["rows"]}
        assert by_account[shop["inventario"]]["debit"] == Decimal("1000.00")
        assert by_account[shop["recepciones"]]["credit"] == Decimal("1000.00")

    async def test_average_cost_blends_receipts(self, shop: dict[str, Any]) -> None:
        await receive(shop, "10", "100")
        await receive(shop, "10", "200")
        assert await product_cost(shop) == Decimal("150.00")

    async def test_delivery_consumes_at_average_and_books_cogs(self, shop: dict[str, Any]) -> None:
        """Entregar 5 con promedio 150: capa -750, COGS 750 y capas == stockxcosto."""
        await receive(shop, "10", "100")
        await receive(shop, "10", "200")
        picking_id = await deliver(shop, "5")

        stock = service(shop)
        assert await stock.on_hand(shop["product_id"], shop["loc_stock"]) == Decimal("15")
        assert await layers_value(shop) == Decimal("2250.00")  # 15 x 150

        [picking] = await RecordSet(shop["env"], "stock.picking").read(
            [picking_id], fields=["move_id"]
        )
        lines = await RecordSet(shop["env"], "account.move.line").search(
            [("move_id", "=", picking["move_id"])], fields=["account_id", "debit", "credit"]
        )
        by_account = {row["account_id"]: row for row in lines["rows"]}
        assert by_account[shop["costo_venta"]]["debit"] == Decimal("750.00")
        assert by_account[shop["inventario"]]["credit"] == Decimal("750.00")

    async def test_cannot_deliver_more_than_on_hand(self, shop: dict[str, Any]) -> None:
        await receive(shop, "3", "100")
        with pytest.raises(StockError) as excinfo:
            await deliver(shop, "5")
        assert excinfo.value.code == "STOCK_INSUFFICIENT"

    async def test_internal_transfer_moves_without_accounting(self, shop: dict[str, Any]) -> None:
        await receive(shop, "10", "100")
        stock = service(shop)
        [second_location] = await RecordSet(shop["env"], "stock.location").create(
            [
                {
                    "name": "BC/Reserva",
                    "location_type": "internal",
                    "warehouse_id": shop["warehouse_id"],
                    "company_id": shop["company_id"],
                }
            ]
        )
        picking_id = await stock.create_picking(
            picking_type="internal",
            date="2026-08-06",
            company_id=shop["company_id"],
            moves=[
                {
                    "product_id": shop["product_id"],
                    "quantity": "4",
                    "location_from_id": shop["loc_stock"],
                    "location_to_id": second_location,
                }
            ],
        )
        await stock.action_validate(picking_id)
        assert await stock.on_hand(shop["product_id"], shop["loc_stock"]) == Decimal("6")
        assert await stock.on_hand(shop["product_id"], second_location) == Decimal("4")
        assert await stock.on_hand_company(shop["product_id"], shop["company_id"]) == Decimal("10")
        [picking] = await RecordSet(shop["env"], "stock.picking").read(
            [picking_id], fields=["move_id"]
        )
        assert picking["move_id"] is None  # trasladar no crea valor

    async def test_inventory_loss_books_against_adjustment_account(
        self, shop: dict[str, Any]
    ) -> None:
        await receive(shop, "10", "100")
        stock = service(shop)
        picking_id = await stock.create_picking(
            picking_type="out",
            date="2026-08-07",
            company_id=shop["company_id"],
            moves=[
                {
                    "product_id": shop["product_id"],
                    "quantity": "2",
                    "location_from_id": shop["loc_stock"],
                    "location_to_id": shop["loc_loss"],
                }
            ],
        )
        await stock.action_validate(picking_id)
        [picking] = await RecordSet(shop["env"], "stock.picking").read(
            [picking_id], fields=["move_id"]
        )
        lines = await RecordSet(shop["env"], "account.move.line").search(
            [("move_id", "=", picking["move_id"])], fields=["account_id", "debit"]
        )
        by_account = {row["account_id"]: row for row in lines["rows"]}
        assert by_account[shop["ajustes_inv"]]["debit"] == Decimal("200.00")

    async def test_done_picking_is_immutable(self, shop: dict[str, Any]) -> None:
        picking_id = await receive(shop, "1", "100")
        with pytest.raises(StockError) as excinfo:
            await service(shop).action_cancel(picking_id)
        assert excinfo.value.code == "STOCK_DONE_IMMUTABLE"

    async def test_service_products_never_move_stock(self, shop: dict[str, Any]) -> None:
        with pytest.raises(StockError) as excinfo:
            await service(shop).create_picking(
                picking_type="in",
                date="2026-08-05",
                company_id=shop["company_id"],
                moves=[
                    {
                        "product_id": shop["service_id"],
                        "quantity": "1",
                        "location_from_id": shop["loc_supplier"],
                        "location_to_id": shop["loc_stock"],
                        "price_unit": Decimal("10"),
                    }
                ],
            )
        assert excinfo.value.code == "STOCK_SERVICE_PRODUCT"

    async def test_tracked_product_requires_lot(self, shop: dict[str, Any]) -> None:
        await RecordSet(shop["env"], "product.product").write(
            [shop["product_id"]], {"tracking": "lot"}
        )
        with pytest.raises(StockError) as excinfo:
            await receive(shop, "5", "100", validate=False)
        assert excinfo.value.code == "STOCK_LOT_REQUIRED"

    async def test_receipt_without_price_is_rejected(self, shop: dict[str, Any]) -> None:
        stock = service(shop)
        picking_id = await stock.create_picking(
            picking_type="in",
            date="2026-08-05",
            company_id=shop["company_id"],
            moves=[
                {
                    "product_id": shop["product_id"],
                    "quantity": "5",
                    "location_from_id": shop["loc_supplier"],
                    "location_to_id": shop["loc_stock"],
                }
            ],
        )
        with pytest.raises(StockError) as excinfo:
            await stock.action_validate(picking_id)
        assert excinfo.value.code == "STOCK_PRICE_REQUIRED"


class TestReportsAndBooks:
    async def test_on_hand_report_matches_the_layers(self, shop: dict[str, Any]) -> None:
        await receive(shop, "10", "100")
        await receive(shop, "10", "200")
        await deliver(shop, "5")
        result = await run_report(shop["env"], "stock.on_hand", {"company_id": shop["company_id"]})
        [row] = result["rows"]
        assert Decimal(row["quantity"]) == Decimal("15")
        assert Decimal(row["value"]) == Decimal("2250.00")
        assert Decimal(result["total_value"]) == await layers_value(shop)

    async def test_trial_balance_still_balances(self, shop: dict[str, Any]) -> None:
        """Después de recibir, entregar y ajustar, los libros siguen cuadrando."""
        await receive(shop, "10", "100")
        await deliver(shop, "3")
        result = await run_report(
            shop["env"], "account.trial_balance", {"company_id": shop["company_id"]}
        )
        assert result["balanced"] is True
