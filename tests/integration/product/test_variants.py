"""Generar la matriz contra la base real: idempotencia, herencia y stock."""

from decimal import Decimal
from typing import Any

import pytest
from modules.product.services import ProductError
from modules.stock.services import StockError, StockService
from ordo_core.actions import dispatch
from ordo_core.recordset import RecordSet
from ordo_core.reports import run_report

from tests.integration.product.test_catalog import build_catalog

pytestmark = pytest.mark.integration


async def generate(shop: dict[str, Any], template_id: int, **params: Any) -> dict[str, Any]:
    return await dispatch(
        shop["env"], "product.template", "action_generate_variants", template_id, params
    )


async def receive(shop: dict[str, Any], product_id: int, quantity: str, cost: str) -> None:
    """Entrada valorizada, como la haría una recepción de proveedor."""
    service = StockService(shop["env"])
    picking_id = await service.create_picking(
        picking_type="in",
        date="2026-08-05",
        company_id=shop["company_id"],
        partner_id=shop["vendor_id"],
        origin="Carga inicial",
        moves=[
            {
                "product_id": product_id,
                "quantity": quantity,
                "location_from_id": shop["loc_supplier"],
                "location_to_id": shop["loc_stock"],
                "price_unit": Decimal(cost),
            }
        ],
    )
    await service.action_validate(picking_id)


class TestGeneration:
    async def test_the_matrix_is_the_cartesian_product(self, shop: dict[str, Any]) -> None:
        catalog = await build_catalog(shop)
        result = await generate(shop, catalog["template_id"])
        assert result["created"] == 4  # 2 tallas x 2 colores

        variants = await RecordSet(shop["env"], "product.product").search(
            [("template_id", "=", catalog["template_id"])],
            fields=["variant_label", "default_code", "product_type", "category_id"],
        )
        labels = {row["variant_label"] for row in variants["rows"]}
        assert labels == {"S / Rojo", "S / Negro", "M / Rojo", "M / Negro"}

        codes = {row["default_code"] for row in variants["rows"]}
        assert "POL-OVR-M-ROJ" in codes

        # lo que la variante hereda del modelo al nacer
        assert {row["product_type"] for row in variants["rows"]} == {"consu"}
        assert {row["category_id"] for row in variants["rows"]} == {catalog["category_id"]}

    async def test_regenerating_creates_nothing(self, shop: dict[str, Any]) -> None:
        """Es la operación normal: la tienda vuelve a pedir la matriz en octubre."""
        catalog = await build_catalog(shop)
        await generate(shop, catalog["template_id"])
        again = await generate(shop, catalog["template_id"])
        assert again["created"] == 0
        assert again["existing"] == 4

    async def test_adding_a_size_only_creates_the_missing_ones(self, shop: dict[str, Any]) -> None:
        catalog = await build_catalog(shop)
        await generate(shop, catalog["template_id"])

        [l_id] = await RecordSet(shop["env"], "product.attribute.value").create(
            [
                {
                    "attribute_id": catalog["talla_id"],
                    "name": "L",
                    "code": "L",
                    "sequence": 30,
                    "company_id": shop["company_id"],
                }
            ]
        )
        axis = await RecordSet(shop["env"], "product.template.attribute.line").search(
            [
                ("template_id", "=", catalog["template_id"]),
                ("attribute_id", "=", catalog["talla_id"]),
            ],
            fields=["id", "value_ids"],
        )
        line = axis["rows"][0]
        await RecordSet(shop["env"], "product.template.attribute.line").write(
            [line["id"]], {"value_ids": f"{line['value_ids']},{l_id}"}
        )

        grown = await generate(shop, catalog["template_id"])
        assert grown["created"] == 2  # L/Rojo y L/Negro, nada más
        assert grown["existing"] == 4

    async def test_archived_variants_are_not_resurrected(self, shop: dict[str, Any]) -> None:
        """Archivar es una decisión; regenerar no debe deshacerla en silencio."""
        catalog = await build_catalog(shop)
        created = await generate(shop, catalog["template_id"])
        victim = created["product_ids"][0]
        await dispatch(shop["env"], "product.product", "action_archive", victim, {})

        again = await generate(shop, catalog["template_id"])
        assert again["created"] == 0

    async def test_surcharge_by_attribute_value_is_decimal(self, shop: dict[str, Any]) -> None:
        catalog = await build_catalog(shop)
        await generate(
            shop, catalog["template_id"], price_by_value={str(catalog["m_id"]): "1500.00"}
        )
        variants = await RecordSet(shop["env"], "product.product").search(
            [("template_id", "=", catalog["template_id"])],
            fields=["variant_label", "list_price"],
        )
        prices = {row["variant_label"]: row["list_price"] for row in variants["rows"]}
        assert prices["S / Rojo"] == Decimal("19990.00")
        assert prices["M / Rojo"] == Decimal("21490.00")


class TestGenerationRefusals:
    async def test_a_template_without_axes_has_no_matrix(self, shop: dict[str, Any]) -> None:
        [template_id] = await RecordSet(shop["env"], "product.template").create(
            [
                {
                    "name": "Modelo suelto",
                    "default_code": None,
                    "category_id": None,
                    "product_type": "consu",
                    "uom_id": None,
                    "list_price": Decimal("1000.00"),
                    "tracking": "none",
                    "income_account_id": None,
                    "expense_account_id": None,
                    "description": None,
                    "company_id": shop["company_id"],
                }
            ]
        )
        with pytest.raises(ProductError) as excinfo:
            await generate(shop, template_id)
        assert excinfo.value.code == "PRODUCT_TEMPLATE_NO_ATTRIBUTES"

    async def test_a_value_from_another_attribute_is_rejected(self, shop: dict[str, Any]) -> None:
        """ "Rojo" colgado del eje de talla daría una matriz sin sentido y muda."""
        catalog = await build_catalog(shop)
        axis = await RecordSet(shop["env"], "product.template.attribute.line").search(
            [
                ("template_id", "=", catalog["template_id"]),
                ("attribute_id", "=", catalog["talla_id"]),
            ],
            fields=["id"],
        )
        await RecordSet(shop["env"], "product.template.attribute.line").write(
            [axis["rows"][0]["id"]], {"value_ids": f"{catalog['s_id']},{catalog['rojo_id']}"}
        )
        with pytest.raises(ProductError) as excinfo:
            await generate(shop, catalog["template_id"])
        assert excinfo.value.code == "PRODUCT_ATTRIBUTE_VALUE_UNKNOWN"

    async def test_an_unknown_template_is_a_404(self, shop: dict[str, Any]) -> None:
        with pytest.raises(ProductError) as excinfo:
            await generate(shop, 987654)
        assert excinfo.value.code == "PRODUCT_TEMPLATE_NOT_FOUND"


class TestVariantsAreIndependentInStock:
    async def test_receiving_one_variant_leaves_the_others_at_zero(
        self, shop: dict[str, Any]
    ) -> None:
        """Cada talla-color es su propio producto: su stock y su costo promedio."""
        catalog = await build_catalog(shop)
        created = await generate(shop, catalog["template_id"])
        first = created["product_ids"][0]
        await receive(shop, first, "10", "8000")

        matrix = await run_report(
            shop["env"],
            "stock.variant_matrix",
            {"template_id": catalog["template_id"], "company_id": shop["company_id"]},
        )
        quantities = {row["product_id"]: row["quantity"] for row in matrix["rows"]}
        assert quantities[first] == "10"
        assert sum(int(value) for value in quantities.values()) == 10
        assert matrix["total_value"] == "80000.00"
        # el reporte muestra también las agotadas: en moda, esa es la fila que importa
        assert len(matrix["rows"]) == 4
        assert [axis["name"] for axis in matrix["axes"]] == ["Talla", "Color"]

    async def test_a_variant_with_stock_cannot_be_archived(self, shop: dict[str, Any]) -> None:
        catalog = await build_catalog(shop)
        created = await generate(shop, catalog["template_id"])
        first = created["product_ids"][0]
        await receive(shop, first, "3", "8000")

        with pytest.raises(StockError) as excinfo:
            await dispatch(shop["env"], "product.product", "action_archive", first, {})
        assert excinfo.value.code == "PRODUCT_VARIANT_HAS_STOCK"

        # la de al lado, sin existencias, sí se archiva
        empty = created["product_ids"][1]
        result = await dispatch(shop["env"], "product.product", "action_archive", empty, {})
        assert result["active"] is False
