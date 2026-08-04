"""Acciones de inventario expuestas a la API."""

from __future__ import annotations

from typing import Any

from ordo_core.actions import action
from ordo_core.environment import Environment

from modules.stock.services import StockService


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
