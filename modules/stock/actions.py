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


@action(
    "stock.reorder.rule",
    "action_replenish",
    summary=(
        "Repone por traslado interno hasta el objetivo: crea el picking y lo "
        "valida en la misma operación"
    ),
    # Sin aprobación: un traslado entre ubicaciones internas no cambia el valor
    # del inventario ni saca nada de la compañía.
)
async def replenish(env: Environment, record_id: int, params: dict[str, Any]) -> dict[str, Any]:
    from modules.stock.reorder import ReorderService

    return await ReorderService(env).action_replenish(record_id)


@action(
    "product.template",
    "action_apply_reorder_rules",
    summary="Propaga los niveles de reposición a todas las variantes del modelo",
    params={
        "location_id": "Ubicación vigilada, normalmente la sala de ventas (obligatorio)",
        "min_quantity": "Nivel que dispara la reposición, string decimal (obligatorio)",
        "max_quantity": "Nivel objetivo al reponer, string decimal (obligatorio)",
        "route": "internal para trasladar desde bodega, buy para comprar",
        "source_location_id": "Bodega que surte, si la ruta es traslado",
        "supplier_id": "Proveedor, si la ruta es compra",
        "multiple_quantity": "Redondea a múltiplos de esta cantidad, como una caja de 12",
    },
)
async def apply_reorder_rules(
    env: Environment, record_id: int, params: dict[str, Any]
) -> dict[str, Any]:
    from modules.stock.reorder import ReorderService

    for name in ("location_id", "min_quantity", "max_quantity"):
        if params.get(name) in (None, ""):
            raise StockError(
                "STOCK_RULE_INVALID_RANGE",
                f"Falta el parámetro {name}",
                hint="location_id, min_quantity y max_quantity son obligatorios.",
            )
    return await ReorderService(env).apply_to_variants(
        record_id,
        location_id=int(params["location_id"]),
        min_quantity=str(params["min_quantity"]),
        max_quantity=str(params["max_quantity"]),
        route=str(params.get("route") or "internal"),
        source_location_id=(
            int(params["source_location_id"]) if params.get("source_location_id") else None
        ),
        supplier_id=int(params["supplier_id"]) if params.get("supplier_id") else None,
        multiple_quantity=(
            str(params["multiple_quantity"]) if params.get("multiple_quantity") else None
        ),
    )
