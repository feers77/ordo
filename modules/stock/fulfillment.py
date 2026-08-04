"""Cumplimiento: la orden comercial se vuelve movimiento de stock.

Entregar una venta o recibir una compra crea el picking desde las líneas
con producto almacenable y lo valida en la misma operación: stock, capas y
asiento de una vez. Las líneas de servicio se ignoran — un servicio no se
despacha en camión.
"""

from __future__ import annotations

from typing import Any

from ordo_core.environment import Environment
from ordo_core.recordset import RecordSet

from modules.stock.services import StockError, StockService


async def _default_location(env: Environment, company_id: int, location_type: str) -> int:
    result = await RecordSet(env, "stock.location").search(
        [("company_id", "=", company_id), ("location_type", "=", location_type)],
        fields=["id"],
        limit=2,
    )
    rows = result["rows"]
    if not rows:
        raise StockError(
            "STOCK_NO_LOCATION",
            f"No existe una ubicación de tipo {location_type} en la compañía",
            hint="Crea las ubicaciones del almacén antes de operar.",
        )
    return int(rows[0]["id"])


async def _stockable_lines(
    env: Environment, line_model: str, order_id: int
) -> list[dict[str, Any]]:
    result = await RecordSet(env, line_model).search(
        [("order_id", "=", order_id), ("product_id", "!=", None)],
        fields=["id", "product_id", "quantity", "price_unit", "name"],
        limit=500,
    )
    products = RecordSet(env, "product.product")
    lines = []
    for row in sorted(result["rows"], key=lambda item: item["id"]):
        [product] = await products.read([row["product_id"]], fields=["product_type"])
        if product["product_type"] == "consu":
            lines.append(row)
    if not lines:
        raise StockError(
            "STOCK_NOTHING_TO_MOVE",
            "La orden no tiene líneas con producto almacenable",
            hint="Los servicios no se despachan; agrega product_id almacenable.",
        )
    return lines


async def deliver_sale(
    env: Environment,
    order_id: int,
    *,
    location_from_id: int | None = None,
) -> dict[str, Any]:
    """Entrega la orden de venta: picking out validado al costo promedio."""
    [order] = await RecordSet(env, "sale.order").read(
        [order_id], fields=["id", "name", "state", "partner_id", "company_id", "date_order"]
    )
    if order["state"] not in ("confirmed", "invoiced", "credited"):
        raise StockError(
            "STOCK_ORDER_NOT_READY",
            "Solo se entrega una orden confirmada o facturada",
            hint="Confirma la orden primero con action_confirm.",
        )
    lines = await _stockable_lines(env, "sale.order.line", order_id)
    origin = location_from_id or await _default_location(env, order["company_id"], "internal")
    destination = await _default_location(env, order["company_id"], "customer")

    service = StockService(env)
    picking_id = await service.create_picking(
        picking_type="out",
        date=str(order["date_order"]),
        company_id=order["company_id"],
        partner_id=order["partner_id"],
        origin=order["name"],
        moves=[
            {
                "product_id": line["product_id"],
                "quantity": line["quantity"],
                "location_from_id": origin,
                "location_to_id": destination,
            }
            for line in lines
        ],
    )
    number = await service.action_validate(picking_id)
    return {"picking_id": picking_id, "name": number, "moves": len(lines)}


async def receive_purchase(
    env: Environment,
    order_id: int,
    *,
    location_to_id: int | None = None,
) -> dict[str, Any]:
    """Recibe la orden de compra: picking in al costo de la línea."""
    [order] = await RecordSet(env, "purchase.order").read(
        [order_id], fields=["id", "name", "state", "partner_id", "company_id", "date_order"]
    )
    if order["state"] not in ("confirmed", "billed", "credited"):
        raise StockError(
            "STOCK_ORDER_NOT_READY",
            "Solo se recibe una orden confirmada o facturada",
            hint="Confirma la orden primero con action_confirm.",
        )
    lines = await _stockable_lines(env, "purchase.order.line", order_id)
    destination = location_to_id or await _default_location(env, order["company_id"], "internal")
    origin = await _default_location(env, order["company_id"], "supplier")

    service = StockService(env)
    picking_id = await service.create_picking(
        picking_type="in",
        date=str(order["date_order"]),
        company_id=order["company_id"],
        partner_id=order["partner_id"],
        origin=order["name"],
        moves=[
            {
                "product_id": line["product_id"],
                "quantity": line["quantity"],
                "location_from_id": origin,
                "location_to_id": destination,
                "price_unit": line["price_unit"],
            }
            for line in lines
        ],
    )
    number = await service.action_validate(picking_id)
    return {"picking_id": picking_id, "name": number, "moves": len(lines)}
