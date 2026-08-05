"""Acciones de inventario expuestas a la API."""

from __future__ import annotations

from typing import Any

from ordo_core.actions import action
from ordo_core.environment import Environment
from ordo_core.recordset import RecordSet

from modules.stock.services import StockError, StockService


@action(
    "product.product",
    "action_archive",
    summary="Archiva un producto o variante; se niega si todavía tiene existencias",
)
async def archive_product(
    env: Environment, record_id: int, params: dict[str, Any]
) -> dict[str, Any]:
    """Vive en `stock` y no en `product` porque la restricción es de inventario.

    Archivar algo que todavía está en la bodega deja existencias sin producto
    visible: el inventario contable sigue contándolas y el físico no las
    encuentra. La flecha de dependencia también manda —`stock` conoce
    `product`, nunca al revés—.
    """
    products = RecordSet(env, "product.product")
    rows = await products.read([record_id], fields=["id", "name", "company_id", "active"])
    if not rows:
        raise StockError(
            "PRODUCT_NOT_FOUND",
            f"No existe el producto {record_id}",
            hint="Revisa el id contra product.product.",
        )
    product = rows[0]
    if not product["active"]:
        return {"product_id": record_id, "active": False, "already_archived": True}

    quantity = await StockService(env).on_hand_company(record_id, product["company_id"])
    if quantity != 0:
        raise StockError(
            "PRODUCT_VARIANT_HAS_STOCK",
            f"'{product['name']}' todavía tiene {quantity} unidades en bodega",
            hint=(
                "Agótalo vendiéndolo o ajústalo a cero contra la ubicación de "
                "ajuste antes de archivarlo."
            ),
        )
    await products.write([record_id], {"active": False})
    return {"product_id": record_id, "active": False, "already_archived": False}


@action(
    "stock.picking",
    "action_validate",
    summary="Valida el picking: mueve el stock, valoriza y asienta en una operación",
)
async def validate(env: Environment, record_id: int, params: dict[str, Any]) -> dict[str, Any]:
    return {"name": await StockService(env).action_validate(record_id), "state": "done"}


@action(
    "stock.picking",
    "action_cancel",
    summary="Cancela un picking en borrador (uno hecho se revierte con el inverso)",
)
async def cancel(env: Environment, record_id: int, params: dict[str, Any]) -> dict[str, Any]:
    await StockService(env).action_cancel(record_id)
    return {"state": "cancelled"}
