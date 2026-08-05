"""El ticket mueve stock: un picking por ticket, no uno por turno.

Agregar los movimientos al cierre dejaría la bodega mintiendo durante todo el
turno, y la alerta de reposición llegaría cuando ya no queda nada que reponer.

Vive en `modules/pos` y no en `modules/stock` porque `pos` conoce a `stock` y
nunca al revés; copiar aquí el patrón de `stock/fulfillment.py` cuesta unas
líneas y mantiene la flecha de dependencia derecha.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from ordo_core.environment import Environment
from ordo_core.recordset import RecordSet

from modules.stock.fulfillment import default_location
from modules.stock.services import StockError, StockService

ZERO = Decimal("0")


async def _stockable_lines(env: Environment, order_id: int) -> list[dict[str, Any]]:
    """Las líneas con producto almacenable. Un servicio no se despacha."""
    result = await RecordSet(env, "pos.order.line").search(
        [("order_id", "=", order_id)],
        fields=["id", "product_id", "quantity", "name"],
        limit=200,
    )
    products = RecordSet(env, "product.product")
    lines = []
    for row in sorted(result["rows"], key=lambda item: item["id"]):
        [product] = await products.read([row["product_id"]], fields=["product_type"])
        if product["product_type"] == "consu":
            lines.append(row)
    return lines


async def deliver_ticket(env: Environment, order_id: int) -> int | None:
    """Saca la mercadería del ticket hacia el cliente. Devuelve el picking.

    Un ticket solo de servicios no mueve nada y devuelve `None`: no es un error,
    es una venta sin bodega.
    """
    [order] = await RecordSet(env, "pos.order").read(
        [order_id], fields=["id", "name", "partner_id", "company_id", "date_order", "session_id"]
    )
    lines = await _stockable_lines(env, order_id)
    if not lines:
        return None

    origin_location = await _register_location(env, order["session_id"])
    destination = await default_location(env, order["company_id"], "customer")

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
                "location_from_id": origin_location,
                "location_to_id": destination,
            }
            for line in lines
        ],
    )
    await service.action_validate(picking_id)
    return picking_id


async def return_ticket(env: Environment, refund_id: int, original_id: int) -> int | None:
    """Devuelve la mercadería a la sala, **al costo con que salió**.

    No al promedio vigente. Si entre la venta y la devolución llegó un lote más
    caro, valorizar la devolución al promedio nuevo infla el inventario y regala
    margen; si llegó uno más barato, lo desinfla. El costo correcto está en la
    capa de valorización que generó la salida original.
    """
    [refund] = await RecordSet(env, "pos.order").read(
        [refund_id],
        fields=["id", "name", "partner_id", "company_id", "date_order", "session_id"],
    )
    lines = await _stockable_lines(env, refund_id)
    if not lines:
        return None

    costs = await original_costs(env, original_id)
    destination = await _register_location(env, refund["session_id"])
    source = await default_location(env, refund["company_id"], "customer")

    moves = []
    for line in lines:
        unit_cost = costs.get(line["product_id"])
        if unit_cost is None:
            raise StockError(
                "POS_REFUND_NO_LAYER",
                f"No se encuentra el costo original de '{line['name']}'",
                hint=(
                    "Una devolución entra al costo con que salió. Si el ticket "
                    "original no movió stock, no hay nada que devolver."
                ),
            )
        moves.append(
            {
                "product_id": line["product_id"],
                # La cantidad de la línea de devolución es negativa; el
                # movimiento de entrada la lleva en positivo.
                "quantity": str(abs(Decimal(line["quantity"]))),
                "location_from_id": source,
                "location_to_id": destination,
                "price_unit": unit_cost,
            }
        )

    service = StockService(env)
    picking_id = await service.create_picking(
        picking_type="in",
        date=str(refund["date_order"]),
        company_id=refund["company_id"],
        partner_id=refund["partner_id"],
        origin=refund["name"],
        moves=moves,
    )
    await service.action_validate(picking_id)
    return picking_id


async def original_costs(env: Environment, order_id: int) -> dict[int, Decimal]:
    """Costo unitario con que salió cada producto del ticket original."""
    [order] = await RecordSet(env, "pos.order").read([order_id], fields=["picking_id"])
    if not order["picking_id"]:
        return {}
    moves = await RecordSet(env, "stock.move").search(
        [("picking_id", "=", order["picking_id"])], fields=["id", "product_id"], limit=200
    )
    move_ids = [row["id"] for row in moves["rows"]]
    if not move_ids:
        return {}
    layers = await RecordSet(env, "stock.valuation.layer").search(
        [("stock_move_id", "in", move_ids)],
        fields=["stock_move_id", "product_id", "unit_cost"],
        limit=len(move_ids) * 2,
    )
    return {row["product_id"]: Decimal(str(row["unit_cost"] or ZERO)) for row in layers["rows"]}


async def _register_location(env: Environment, session_id: int) -> int:
    """La sala de ventas de la caja del turno, no la bodega central."""
    [session] = await RecordSet(env, "pos.session").read([session_id], fields=["config_id"])
    [config] = await RecordSet(env, "pos.config").read(
        [session["config_id"]], fields=["location_id"]
    )
    return int(config["location_id"])
