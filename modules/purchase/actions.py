"""Acciones de compras expuestas a la API."""

from __future__ import annotations

from typing import Any

from ordo_core.actions import action
from ordo_core.environment import Environment
from ordo_core.errors import KernelError

from modules.purchase.services import PurchaseService


@action(
    "purchase.order",
    "action_confirm",
    summary="Confirma la orden: fija totales y asigna número",
)
async def confirm(env: Environment, record_id: int, params: dict[str, Any]) -> dict[str, Any]:
    return {"name": await PurchaseService(env).action_confirm(record_id)}


@action(
    "purchase.order",
    "action_bill",
    summary="Registra la factura del proveedor y la contabiliza",
    requires_approval=True,
    params={"vendor_ref": "Número de la factura del proveedor (obligatorio)"},
)
async def bill(env: Environment, record_id: int, params: dict[str, Any]) -> dict[str, Any]:
    vendor_ref = str(params.get("vendor_ref", ""))
    if not vendor_ref.strip():
        raise KernelError(
            "PURCHASE_VENDOR_REF_REQUIRED",
            "Falta el número de la factura del proveedor",
            hint="Pasa params.vendor_ref con el número del documento recibido.",
        )
    move_id = await PurchaseService(env).action_bill(record_id, vendor_ref=vendor_ref)
    return {"move_id": move_id}


@action(
    "purchase.order",
    "action_cancel",
    summary="Cancela una orden sin factura registrada",
)
async def cancel(env: Environment, record_id: int, params: dict[str, Any]) -> dict[str, Any]:
    await PurchaseService(env).action_cancel(record_id)
    return {"state": "cancelled"}
