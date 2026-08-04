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
    "action_cancel",
    summary="Cancela una orden no facturada",
)
async def cancel(env: Environment, record_id: int, params: dict[str, Any]) -> dict[str, Any]:
    await SaleService(env).action_cancel(record_id)
    return {"state": "cancelled"}
