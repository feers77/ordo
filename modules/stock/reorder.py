"""Materializar la reposición: de la alerta al traslado.

Hasta aquí `stock.reorder.rule` solo avisaba. Avisar sin poder actuar deja el
trabajo real —crear el picking, elegir el origen, redondear a caja completa— en
manos de quien lea la alerta, que es exactamente lo que un ERP debería hacer por
él.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from ordo_core.environment import Environment
from ordo_core.recordset import RecordSet

from modules.stock.replenishment import ZERO, ReplenishError, suggested_quantity
from modules.stock.services import StockService

RULE_FIELDS = (
    "id",
    "product_id",
    "location_id",
    "min_quantity",
    "max_quantity",
    "route",
    "source_location_id",
    "supplier_id",
    "multiple_quantity",
    "company_id",
)


class ReorderService:
    def __init__(self, env: Environment) -> None:
        self.env = env
        self.rules = RecordSet(env, "stock.reorder.rule")
        self.stock = StockService(env)

    async def rule(self, rule_id: int) -> dict[str, Any]:
        rows = await self.rules.read([rule_id], fields=list(RULE_FIELDS))
        if not rows:
            raise ReplenishError(
                "STOCK_RULE_NOT_FOUND",
                f"No existe la regla {rule_id}",
                hint="Revisa el id contra stock.reorder.rule.",
            )
        return rows[0]

    async def needed(self, rule: dict[str, Any]) -> Decimal:
        """Cuánto falta para volver al objetivo. Cero si no hace falta reponer."""
        on_hand = await self.stock.on_hand(rule["product_id"], rule["location_id"])
        multiple = rule["multiple_quantity"]
        return suggested_quantity(
            on_hand,
            Decimal(rule["min_quantity"]),
            Decimal(rule["max_quantity"]),
            multiple=Decimal(multiple) if multiple else None,
        )

    async def action_replenish(self, rule_id: int) -> dict[str, Any]:
        """Repone por traslado interno: crea el picking y lo valida.

        Solo la ruta de traslado. Comprar es crear una orden de compra, y eso
        vive en `modules/purchase`, que es quien conoce ese documento: `stock`
        no puede depender de él sin invertir la flecha.
        """
        rule = await self.rule(rule_id)
        if rule["route"] != "internal":
            raise ReplenishError(
                "STOCK_REPLENISH_NO_SOURCE",
                "La regla repone comprando, no trasladando",
                hint="Usa action_replenish_buy, que crea la orden de compra.",
            )
        if not rule["source_location_id"]:
            raise ReplenishError(
                "STOCK_REPLENISH_NO_SOURCE",
                "La regla no declara desde qué ubicación reponer",
                hint="Fija source_location_id con la bodega que surte a esta tienda.",
            )

        quantity = await self.needed(rule)
        if quantity == ZERO:
            raise ReplenishError(
                "STOCK_REPLENISH_NOT_NEEDED",
                "El stock todavía está sobre el mínimo: no hay nada que reponer",
                hint="La regla se dispara bajo el mínimo, no bajo el máximo.",
            )

        available = await self.stock.on_hand(rule["product_id"], rule["source_location_id"])
        if available < quantity:
            raise ReplenishError(
                "STOCK_REPLENISH_SOURCE_EMPTY",
                f"El origen tiene {available} y hacen falta {quantity}",
                hint=("Compra al proveedor o traslada una cantidad menor indicándola a mano."),
            )

        [product] = await RecordSet(self.env, "product.product").read(
            [rule["product_id"]], fields=["name"]
        )
        picking_id = await self.stock.create_picking(
            picking_type="internal",
            date=datetime.now(UTC).date().isoformat(),
            company_id=rule["company_id"],
            partner_id=None,
            origin=f"Reposición {product['name']}",
            moves=[
                {
                    "product_id": rule["product_id"],
                    "quantity": str(quantity),
                    "location_from_id": rule["source_location_id"],
                    "location_to_id": rule["location_id"],
                }
            ],
        )
        number = await self.stock.action_validate(picking_id)
        return {
            "rule_id": rule_id,
            "route": "internal",
            "picking_id": picking_id,
            "name": number,
            "quantity": str(quantity),
        }

    async def apply_to_variants(
        self,
        template_id: int,
        *,
        location_id: int,
        min_quantity: str,
        max_quantity: str,
        route: str = "internal",
        source_location_id: int | None = None,
        supplier_id: int | None = None,
        multiple_quantity: str | None = None,
    ) -> dict[str, Any]:
        """Propaga min/max a todas las variantes activas del modelo.

        Crear a mano sesenta reglas —diez modelos por seis variantes— es
        inviable, y una tienda que no las crea se queda sin la mitad de las
        tallas sin enterarse.
        """
        variants = await RecordSet(self.env, "product.product").search(
            [("template_id", "=", template_id)],
            fields=["id", "company_id"],
            limit=500,
        )
        if not variants["rows"]:
            raise ReplenishError(
                "STOCK_RULE_NO_VARIANTS",
                f"El modelo {template_id} no tiene variantes activas",
                hint="Genera la matriz con action_generate_variants antes de aplicar reglas.",
            )
        existing = await self.rules.search(
            [
                ("product_id", "in", [row["id"] for row in variants["rows"]]),
                ("location_id", "=", location_id),
            ],
            fields=["id", "product_id"],
            limit=500,
            active_test=False,
        )
        by_product = {row["product_id"]: row["id"] for row in existing["rows"]}

        values = {
            "min_quantity": min_quantity,
            "max_quantity": max_quantity,
            "route": route,
            "source_location_id": source_location_id,
            "supplier_id": supplier_id,
            "multiple_quantity": multiple_quantity,
            "active": True,
        }
        created = []
        updated = []
        for variant in variants["rows"]:
            rule_id = by_product.get(variant["id"])
            if rule_id is not None:
                await self.rules.write([rule_id], values)
                updated.append(rule_id)
                continue
            [new_id] = await self.rules.create(
                [
                    {
                        "product_id": variant["id"],
                        "location_id": location_id,
                        "company_id": variant["company_id"],
                        **values,
                    }
                ]
            )
            created.append(new_id)
        return {
            "template_id": template_id,
            "created": len(created),
            "updated": len(updated),
            "rule_ids": [*created, *updated],
        }
