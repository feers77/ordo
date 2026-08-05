"""Generación de la matriz de variantes.

Regenerar es la operación normal: una tienda agrega la talla XL en octubre y
vuelve a pedir la matriz. Por eso `action_generate_variants` es idempotente —
crea las que faltan y no toca las que existen— en vez de fallar o duplicar.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from ordo_core.environment import Environment
from ordo_core.recordset import RecordSet

from modules.product.variants import (
    VariantError,
    combinations,
    compose_label,
    compose_sku,
    parse_value_ids,
)

# Lo que una variante copia de su modelo al nacer. Es una copia deliberada y no
# un `related`: el kernel resuelve `related` como compute no almacenado, y un
# catálogo cuyo nombre no vive en una columna no se puede filtrar ni ordenar en
# SQL (ADR-018).
INHERITED = (
    "product_type",
    "uom_id",
    "tracking",
    "income_account_id",
    "expense_account_id",
    "category_id",
)

TEMPLATE_FIELDS = (
    "id",
    "name",
    "default_code",
    "list_price",
    "description",
    "active",
    "company_id",
    *INHERITED,
)


class ProductError(VariantError):
    """Error del catálogo con código estable."""


class VariantService:
    def __init__(self, env: Environment) -> None:
        self.env = env
        self.templates = RecordSet(env, "product.template")
        self.lines = RecordSet(env, "product.template.attribute.line")
        self.values = RecordSet(env, "product.attribute.value")
        self.products = RecordSet(env, "product.product")
        self.variant_values = RecordSet(env, "product.variant.value")

    # --------------------------------------------------------------- lectura

    async def _template(self, template_id: int) -> dict[str, Any]:
        rows = await self.templates.read([template_id], fields=list(TEMPLATE_FIELDS))
        if not rows:
            raise ProductError(
                "PRODUCT_TEMPLATE_NOT_FOUND",
                f"No existe el modelo {template_id}",
                hint="Crea el product.template antes de generar sus variantes.",
            )
        return rows[0]

    async def _axes(self, template_id: int) -> list[dict[str, Any]]:
        """Los ejes de la matriz, en el orden declarado por `sequence`.

        El orden importa: define cómo se compone "M / Rojo" y el SKU, y si
        cambiara entre dos generaciones la misma variante se vería distinta.
        """
        result = await self.lines.search(
            [("template_id", "=", template_id)],
            fields=["id", "attribute_id", "value_ids", "sequence"],
            limit=50,
        )
        rows = sorted(result["rows"], key=lambda row: (row["sequence"] or 0, row["id"]))
        if not rows:
            raise ProductError(
                "PRODUCT_TEMPLATE_NO_ATTRIBUTES",
                "El modelo no tiene una matriz de variantes declarada",
                hint=(
                    "Crea al menos una product.template.attribute.line con el "
                    "atributo y sus valores antes de generar."
                ),
            )
        axes = []
        for row in rows:
            value_ids = parse_value_ids(row["value_ids"] or "")
            if not value_ids:
                raise ProductError(
                    "PRODUCT_TEMPLATE_NO_ATTRIBUTES",
                    f"El eje del atributo {row['attribute_id']} no tiene valores",
                    hint="Un eje sin valores no genera nada; complétalo o borra la línea.",
                )
            axes.append({**row, "value_ids": value_ids})
        return axes

    async def _value_index(self, axes: list[dict[str, Any]]) -> dict[int, dict[str, Any]]:
        """Los valores de todos los ejes, verificando que sean de su atributo.

        Un valor de otro atributo —"Rojo" colgado del eje de talla— produciría
        una matriz sin sentido y silenciosa: mejor negarse.
        """
        wanted = [value_id for axis in axes for value_id in axis["value_ids"]]
        result = await self.values.search(
            [("id", "in", wanted)],
            fields=["id", "name", "code", "attribute_id"],
            limit=len(wanted) + 1,
        )
        index = {row["id"]: row for row in result["rows"]}
        for axis in axes:
            for value_id in axis["value_ids"]:
                value = index.get(value_id)
                if value is None:
                    raise ProductError(
                        "PRODUCT_ATTRIBUTE_VALUE_UNKNOWN",
                        f"El valor {value_id} no existe",
                        hint="Revisa los ids de value_ids contra product.attribute.value.",
                    )
                if value["attribute_id"] != axis["attribute_id"]:
                    raise ProductError(
                        "PRODUCT_ATTRIBUTE_VALUE_UNKNOWN",
                        (
                            f"El valor {value_id} ('{value['name']}') pertenece al "
                            f"atributo {value['attribute_id']}, no al {axis['attribute_id']}"
                        ),
                        hint="Cada eje solo admite valores de su propio atributo.",
                    )
        return index

    async def _existing(self, template_id: int) -> set[tuple[int, ...]]:
        """Combinaciones que ya existen, incluidas las archivadas.

        Se miran también las inactivas: si no, regenerar resucitaría como
        variante nueva una que alguien archivó a propósito.
        """
        products = await self.products.search(
            [("template_id", "=", template_id)],
            fields=["id"],
            limit=1000,
            active_test=False,
        )
        product_ids = [row["id"] for row in products["rows"]]
        if not product_ids:
            return set()
        values = await self.variant_values.search(
            [("product_id", "in", product_ids)],
            fields=["product_id", "value_id"],
            limit=len(product_ids) * 10,
        )
        by_product: dict[int, list[int]] = {}
        for row in values["rows"]:
            by_product.setdefault(row["product_id"], []).append(row["value_id"])
        return {tuple(sorted(combo)) for combo in by_product.values()}

    # --------------------------------------------------------------- escritura

    async def action_generate_variants(
        self,
        template_id: int,
        *,
        price_by_value: dict[int, Decimal] | None = None,
    ) -> dict[str, Any]:
        template = await self._template(template_id)
        axes = await self._axes(template_id)
        index = await self._value_index(axes)
        wanted = combinations([axis["value_ids"] for axis in axes])
        existing = await self._existing(template_id)

        surcharge = price_by_value or {}
        base_price = template["list_price"] or Decimal("0")
        inherited = {name: template[name] for name in INHERITED}

        pending = [combo for combo in wanted if tuple(sorted(combo)) not in existing]
        if not pending:
            return {
                "template_id": template_id,
                "created": 0,
                "existing": len(wanted),
                "product_ids": [],
            }

        payloads = []
        for combo in pending:
            values = [index[value_id] for value_id in combo]
            label = compose_label([value["name"] for value in values])
            price = base_price
            for value_id in combo:
                price += surcharge.get(value_id, Decimal("0"))
            payloads.append(
                {
                    "name": f"{template['name']} {label}".strip(),
                    "default_code": compose_sku(
                        template["default_code"] or "",
                        [value["code"] or "" for value in values],
                    )
                    or None,
                    "list_price": price,
                    "cost": Decimal("0"),
                    "barcode": None,
                    "description": template["description"],
                    "active": True,
                    "company_id": template["company_id"],
                    "template_id": template_id,
                    "variant_label": label,
                    **inherited,
                }
            )
        product_ids = await self.products.create(payloads)

        memberships = [
            {
                "product_id": product_id,
                "attribute_id": axes[position]["attribute_id"],
                "value_id": value_id,
                "company_id": template["company_id"],
            }
            for product_id, combo in zip(product_ids, pending, strict=True)
            for position, value_id in enumerate(combo)
        ]
        await self.variant_values.create(memberships)

        return {
            "template_id": template_id,
            "created": len(product_ids),
            "existing": len(wanted) - len(product_ids),
            "product_ids": product_ids,
        }
