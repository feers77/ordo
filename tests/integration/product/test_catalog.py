"""El catálogo con variantes contra la base real: las tablas existen y filtran.

La generación de la matriz llega en F12-01b. Aquí se prueba lo que ese trabajo
va a dar por sentado: que la variante se puede anclar a un modelo, que su
pertenencia a un valor de atributo se resuelve en SQL, y que el producto plano
que ya existía sigue funcionando igual.
"""

from decimal import Decimal
from typing import Any

import pytest
from ordo_core.recordset import RecordSet

pytestmark = pytest.mark.integration


async def build_catalog(shop: dict[str, Any]) -> dict[str, Any]:
    """Polera Oversize con ejes Talla (S/M) y Color (Rojo/Negro)."""
    env, company = shop["env"], shop["company_id"]

    [category_id] = await RecordSet(env, "product.category").create(
        [{"name": "Poleras", "parent_id": None, "company_id": company}]
    )
    attributes = RecordSet(env, "product.attribute")
    [talla_id, color_id] = await attributes.create(
        [
            {"name": "Talla", "display_type": "size", "sequence": 10, "company_id": company},
            {"name": "Color", "display_type": "color", "sequence": 20, "company_id": company},
        ]
    )
    values = RecordSet(env, "product.attribute.value")
    [s_id, m_id] = await values.create(
        [
            {
                "attribute_id": talla_id,
                "name": "S",
                "code": "S",
                "sequence": 10,
                "company_id": company,
            },
            {
                "attribute_id": talla_id,
                "name": "M",
                "code": "M",
                "sequence": 20,
                "company_id": company,
            },
        ]
    )
    [rojo_id, negro_id] = await values.create(
        [
            {
                "attribute_id": color_id,
                "name": "Rojo",
                "code": "ROJ",
                "sequence": 10,
                "company_id": company,
            },
            {
                "attribute_id": color_id,
                "name": "Negro",
                "code": "NEG",
                "sequence": 20,
                "company_id": company,
            },
        ]
    )
    [template_id] = await RecordSet(env, "product.template").create(
        [
            {
                "name": "Polera Oversize",
                "default_code": "POL-OVR",
                "category_id": category_id,
                "product_type": "consu",
                "uom_id": None,
                "list_price": Decimal("19990.00"),
                "tracking": "none",
                "income_account_id": shop["ventas"],
                "expense_account_id": None,
                "description": "Algodón peinado, corte oversize",
                "company_id": company,
            }
        ]
    )
    lines = RecordSet(env, "product.template.attribute.line")
    await lines.create(
        [
            {
                "template_id": template_id,
                "attribute_id": talla_id,
                "value_ids": f"{s_id},{m_id}",
                "sequence": 10,
                "company_id": company,
            },
            {
                "template_id": template_id,
                "attribute_id": color_id,
                "value_ids": f"{rojo_id},{negro_id}",
                "sequence": 20,
                "company_id": company,
            },
        ]
    )
    return {
        "category_id": category_id,
        "talla_id": talla_id,
        "color_id": color_id,
        "s_id": s_id,
        "m_id": m_id,
        "rojo_id": rojo_id,
        "negro_id": negro_id,
        "template_id": template_id,
    }


async def make_variant(
    shop: dict[str, Any], catalog: dict[str, Any], talla: int, color: int, label: str, sku: str
) -> int:
    env, company = shop["env"], shop["company_id"]
    [product_id] = await RecordSet(env, "product.product").create(
        [
            {
                "name": f"Polera Oversize {label}",
                "default_code": sku,
                "product_type": "consu",
                "uom_id": None,
                "list_price": Decimal("19990.00"),
                "cost": Decimal("0.00"),
                "tracking": "none",
                "income_account_id": shop["ventas"],
                "expense_account_id": None,
                "barcode": None,
                "company_id": company,
                "description": None,
                "template_id": catalog["template_id"],
                "variant_label": label,
                "category_id": catalog["category_id"],
            }
        ]
    )
    await RecordSet(env, "product.variant.value").create(
        [
            {
                "product_id": product_id,
                "attribute_id": catalog["talla_id"],
                "value_id": talla,
                "company_id": company,
            },
            {
                "product_id": product_id,
                "attribute_id": catalog["color_id"],
                "value_id": color,
                "company_id": company,
            },
        ]
    )
    return product_id


class TestCatalog:
    async def test_template_and_axes_persist(self, shop: dict[str, Any]) -> None:
        catalog = await build_catalog(shop)
        [template] = await RecordSet(shop["env"], "product.template").read(
            [catalog["template_id"]], fields=["name", "list_price", "category_id"]
        )
        assert template["name"] == "Polera Oversize"
        assert template["list_price"] == Decimal("19990.00")

        axes = await RecordSet(shop["env"], "product.template.attribute.line").search(
            [("template_id", "=", catalog["template_id"])],
            fields=["attribute_id", "value_ids"],
        )
        assert len(axes["rows"]) == 2
        # el eje se lee entero: es configuración, no superficie de consulta
        assert all("," in row["value_ids"] for row in axes["rows"])

    async def test_variant_membership_filters_in_sql(self, shop: dict[str, Any]) -> None:
        """La pregunta que hace una tienda de ropa: qué tengo en talla M."""
        catalog = await build_catalog(shop)
        m_rojo = await make_variant(
            shop, catalog, catalog["m_id"], catalog["rojo_id"], "M / Rojo", "POL-OVR-M-ROJ"
        )
        await make_variant(
            shop, catalog, catalog["s_id"], catalog["negro_id"], "S / Negro", "POL-OVR-S-NEG"
        )

        en_m = await RecordSet(shop["env"], "product.variant.value").search(
            [("attribute_id", "=", catalog["talla_id"]), ("value_id", "=", catalog["m_id"])],
            fields=["product_id"],
        )
        assert [row["product_id"] for row in en_m["rows"]] == [m_rojo]

    async def test_variants_hang_from_their_template(self, shop: dict[str, Any]) -> None:
        catalog = await build_catalog(shop)
        await make_variant(
            shop, catalog, catalog["m_id"], catalog["rojo_id"], "M / Rojo", "POL-OVR-M-ROJ"
        )
        await make_variant(
            shop, catalog, catalog["s_id"], catalog["negro_id"], "S / Negro", "POL-OVR-S-NEG"
        )
        variants = await RecordSet(shop["env"], "product.product").search(
            [("template_id", "=", catalog["template_id"])],
            fields=["variant_label", "default_code"],
        )
        assert {row["variant_label"] for row in variants["rows"]} == {"M / Rojo", "S / Negro"}

    async def test_flat_product_still_works(self, shop: dict[str, Any]) -> None:
        """Compatibilidad: el producto sin variantes que sembró build_shop sigue
        siendo un producto normal, con template_id vacío."""
        [product] = await RecordSet(shop["env"], "product.product").read(
            [shop["product_id"]], fields=["name", "template_id", "variant_label"]
        )
        assert product["name"] == "Notebook 14"
        assert product["template_id"] is None
        assert product["variant_label"] is None
