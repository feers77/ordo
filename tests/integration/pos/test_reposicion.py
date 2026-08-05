"""Reposición de bodega a tienda: de la alerta al traslado, sin pasos manuales."""

from decimal import Decimal
from typing import Any

import pytest
from modules.stock.replenishment import ReplenishError
from modules.stock.services import StockService
from ordo_core.actions import dispatch
from ordo_core.recordset import RecordSet
from ordo_core.reports import run_report

from tests.integration.product.test_catalog import build_catalog

pytestmark = pytest.mark.integration


async def receive(shop: dict[str, Any], product_id: int, quantity: str, location: int) -> None:
    service = StockService(shop["env"])
    picking_id = await service.create_picking(
        picking_type="in",
        date="2026-08-04",
        company_id=shop["company_id"],
        partner_id=shop["vendor_id"],
        origin="Carga",
        moves=[
            {
                "product_id": product_id,
                "quantity": quantity,
                "location_from_id": shop["loc_supplier"],
                "location_to_id": location,
                "price_unit": Decimal("8000"),
            }
        ],
    )
    await service.action_validate(picking_id)


async def rule_for(
    shop: dict[str, Any],
    product_id: int,
    *,
    route: str = "internal",
    minimum: str = "5",
    maximum: str = "20",
    multiple: str | None = None,
) -> int:
    [rule_id] = await RecordSet(shop["env"], "stock.reorder.rule").create(
        [
            {
                "product_id": product_id,
                "location_id": shop["loc_store"],
                "min_quantity": minimum,
                "max_quantity": maximum,
                "route": route,
                "source_location_id": shop["loc_stock"] if route == "internal" else None,
                "supplier_id": shop["vendor_id"] if route == "buy" else None,
                "multiple_quantity": multiple,
                "company_id": shop["company_id"],
            }
        ]
    )
    return rule_id


class TestInternalReplenishment:
    async def test_the_transfer_refills_the_shop_from_the_warehouse(
        self, shop: dict[str, Any]
    ) -> None:
        """El 90 % de la reposición en retail es esto, no una compra."""
        await receive(shop, shop["product_id"], "100", shop["loc_stock"])
        await receive(shop, shop["product_id"], "3", shop["loc_store"])
        rule_id = await rule_for(shop, shop["product_id"])

        result = await dispatch(shop["env"], "stock.reorder.rule", "action_replenish", rule_id, {})
        assert result["quantity"] == "17"
        assert result["name"] == "INT/00001"

        service = StockService(shop["env"])
        assert await service.on_hand(shop["product_id"], shop["loc_store"]) == Decimal("20")
        assert await service.on_hand(shop["product_id"], shop["loc_stock"]) == Decimal("83")

    async def test_an_internal_transfer_does_not_change_the_inventory_value(
        self, shop: dict[str, Any]
    ) -> None:
        """Mover mercadería de sitio no la hace valer más ni menos."""
        await receive(shop, shop["product_id"], "100", shop["loc_stock"])
        rule_id = await rule_for(shop, shop["product_id"])
        before = await run_report(
            shop["env"], "account.trial_balance", {"company_id": shop["company_id"]}
        )
        result = await dispatch(shop["env"], "stock.reorder.rule", "action_replenish", rule_id, {})
        [picking] = await RecordSet(shop["env"], "stock.picking").read(
            [result["picking_id"]], fields=["move_id"]
        )
        assert picking["move_id"] is None  # traslado interno: sin asiento

        after = await run_report(
            shop["env"], "account.trial_balance", {"company_id": shop["company_id"]}
        )
        assert after["balanced"] is True
        assert after["rows"] == before["rows"]

    async def test_replenishing_twice_says_there_is_nothing_to_do(
        self, shop: dict[str, Any]
    ) -> None:
        await receive(shop, shop["product_id"], "100", shop["loc_stock"])
        rule_id = await rule_for(shop, shop["product_id"])
        await dispatch(shop["env"], "stock.reorder.rule", "action_replenish", rule_id, {})
        with pytest.raises(ReplenishError) as excinfo:
            await dispatch(shop["env"], "stock.reorder.rule", "action_replenish", rule_id, {})
        assert excinfo.value.code == "STOCK_REPLENISH_NOT_NEEDED"

    async def test_an_empty_warehouse_says_so_instead_of_moving_air(
        self, shop: dict[str, Any]
    ) -> None:
        await receive(shop, shop["product_id"], "2", shop["loc_stock"])
        rule_id = await rule_for(shop, shop["product_id"])
        with pytest.raises(ReplenishError) as excinfo:
            await dispatch(shop["env"], "stock.reorder.rule", "action_replenish", rule_id, {})
        assert excinfo.value.code == "STOCK_REPLENISH_SOURCE_EMPTY"

    async def test_a_rule_without_a_source_cannot_transfer(self, shop: dict[str, Any]) -> None:
        rule_id = await rule_for(shop, shop["product_id"])
        await RecordSet(shop["env"], "stock.reorder.rule").write(
            [rule_id], {"source_location_id": None}
        )
        with pytest.raises(ReplenishError) as excinfo:
            await dispatch(shop["env"], "stock.reorder.rule", "action_replenish", rule_id, {})
        assert excinfo.value.code == "STOCK_REPLENISH_NO_SOURCE"

    async def test_the_multiple_rounds_up_to_a_full_box(self, shop: dict[str, Any]) -> None:
        await receive(shop, shop["product_id"], "100", shop["loc_stock"])
        rule_id = await rule_for(shop, shop["product_id"], maximum="13", multiple="12")
        result = await dispatch(shop["env"], "stock.reorder.rule", "action_replenish", rule_id, {})
        assert result["quantity"] == "24"  # quedarse corto es el error caro


class TestBuyRoute:
    async def test_the_buy_route_leaves_a_draft_purchase_order(self, shop: dict[str, Any]) -> None:
        """Proponer no es comprometer: la orden queda en borrador con su propio
        action_confirm."""
        rule_id = await rule_for(shop, shop["product_id"], route="buy")
        result = await dispatch(
            shop["env"], "stock.reorder.rule", "action_replenish_buy", rule_id, {}
        )
        assert result["quantity"] == "20"
        [order] = await RecordSet(shop["env"], "purchase.order").read(
            [result["purchase_order_id"]], fields=["state", "partner_id"]
        )
        assert order["state"] == "draft"
        assert order["partner_id"] == shop["vendor_id"]

    async def test_the_two_routes_do_not_cross(self, shop: dict[str, Any]) -> None:
        internal = await rule_for(shop, shop["product_id"])
        with pytest.raises(ReplenishError) as excinfo:
            await dispatch(shop["env"], "stock.reorder.rule", "action_replenish_buy", internal, {})
        assert excinfo.value.code == "STOCK_REPLENISH_NO_SOURCE"


class TestVariantAlerts:
    async def test_rules_are_applied_to_the_whole_matrix_at_once(
        self, shop: dict[str, Any]
    ) -> None:
        """Crear sesenta reglas a mano es inviable, y una tienda que no las crea
        se queda sin la mitad de las tallas sin enterarse."""
        catalog = await build_catalog(shop)
        await dispatch(
            shop["env"], "product.template", "action_generate_variants", catalog["template_id"], {}
        )
        result = await dispatch(
            shop["env"],
            "product.template",
            "action_apply_reorder_rules",
            catalog["template_id"],
            {
                "location_id": shop["loc_store"],
                "min_quantity": "5",
                "max_quantity": "20",
                "route": "internal",
                "source_location_id": shop["loc_stock"],
            },
        )
        assert result["created"] == 4
        assert result["updated"] == 0

        again = await dispatch(
            shop["env"],
            "product.template",
            "action_apply_reorder_rules",
            catalog["template_id"],
            {
                "location_id": shop["loc_store"],
                "min_quantity": "8",
                "max_quantity": "30",
            },
        )
        assert again["created"] == 0
        assert again["updated"] == 4  # idempotente: actualiza, no duplica

    async def test_the_alert_breaks_down_by_size(self, shop: dict[str, Any]) -> None:
        """ "Quedan 2 poleras" no sirve; "quedan 0 en talla M" sí."""
        catalog = await build_catalog(shop)
        created = await dispatch(
            shop["env"], "product.template", "action_generate_variants", catalog["template_id"], {}
        )
        await dispatch(
            shop["env"],
            "product.template",
            "action_apply_reorder_rules",
            catalog["template_id"],
            {
                "location_id": shop["loc_store"],
                "min_quantity": "5",
                "max_quantity": "20",
                "route": "internal",
                "source_location_id": shop["loc_stock"],
            },
        )
        # una sola variante tiene stock suficiente; las otras tres están en cero
        stocked = created["product_ids"][0]
        await receive(shop, stocked, "30", shop["loc_store"])
        await receive(shop, created["product_ids"][1], "100", shop["loc_stock"])

        alerts = await run_report(
            shop["env"], "stock.reorder_alerts", {"company_id": shop["company_id"]}
        )
        assert alerts["count"] == 3
        [group] = alerts["by_template"]
        assert group["template_id"] == catalog["template_id"]
        assert len(group["variants"]) == 3
        assert group["total_suggested"] == "60"
        assert all(row["variant_label"] for row in group["variants"])

        # la que tiene stock en bodega se puede reponer; las otras dos no
        plan = await run_report(
            shop["env"],
            "stock.replenishment_plan",
            {"company_id": shop["company_id"], "location_id": shop["loc_store"]},
        )
        assert len(plan["ready"]) == 1
        assert len(plan["blocked"]) == 2
        assert plan["ready"][0]["suggested_action"] == "action_replenish"
        assert plan["ready"][0]["on_hand_source"] == "100"
