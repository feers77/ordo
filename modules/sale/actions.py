"""Acciones de ventas expuestas a la API."""

from __future__ import annotations

from typing import Any

from ordo_core.actions import action
from ordo_core.environment import Environment

from modules.sale.services import SaleService


@action(
    "sale.order",
    "action_confirm",
    summary="Confirma la orden: fija totales y asigna número",
)
async def confirm(env: Environment, record_id: int, params: dict[str, Any]) -> dict[str, Any]:
    return {"name": await SaleService(env).action_confirm(record_id)}


@action(
    "sale.order",
    "action_invoice",
    summary="Factura la orden: crea y contabiliza el asiento en la misma operación",
    requires_approval=True,
)
async def invoice(env: Environment, record_id: int, params: dict[str, Any]) -> dict[str, Any]:
    return {"move_id": await SaleService(env).action_invoice(record_id)}


@action(
    "sale.order",
    "action_credit_note",
    summary="Emite la nota de crédito contable: revierte la factura completa",
    requires_approval=True,
    params={
        "reason": "Motivo de la nota de crédito (obligatorio)",
        "credit_date": "Fecha del asiento de reversión (opcional, ISO)",
    },
)
async def credit_note(env: Environment, record_id: int, params: dict[str, Any]) -> dict[str, Any]:
    reversal_id = await SaleService(env).action_credit_note(
        record_id,
        reason=str(params.get("reason", "")),
        credit_date=params.get("credit_date"),
    )
    return {"credit_note_move_id": reversal_id, "state": "credited"}


@action(
    "sale.order",
    "action_cancel",
    summary="Cancela una orden no facturada",
)
async def cancel(env: Environment, record_id: int, params: dict[str, Any]) -> dict[str, Any]:
    await SaleService(env).action_cancel(record_id)
    return {"state": "cancelled"}


@action(
    "sale.order",
    "action_deliver",
    summary="Entrega la orden: picking de salida validado al costo promedio",
    params={
        "location_from_id": "Ubicación interna de origen (opcional)",
        "warehouse_id": (
            "Almacén desde el que se despacha; basta con esto si el almacén "
            "tiene una sola ubicación interna (opcional)"
        ),
    },
)
async def deliver(env: Environment, record_id: int, params: dict[str, Any]) -> dict[str, Any]:
    from modules.stock.fulfillment import deliver_sale

    location = params.get("location_from_id")
    warehouse = params.get("warehouse_id")
    return await deliver_sale(
        env,
        record_id,
        location_from_id=int(location) if location else None,
        warehouse_id=int(warehouse) if warehouse else None,
    )
