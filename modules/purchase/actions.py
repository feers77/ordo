"""Acciones de compras expuestas a la API."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from ordo_core.actions import action
from ordo_core.environment import Environment
from ordo_core.errors import KernelError
from ordo_core.recordset import RecordSet

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
    "action_credit_note",
    summary="Registra la nota de crédito del proveedor: revierte su factura",
    requires_approval=True,
    params={
        "reason": "Motivo de la nota de crédito (obligatorio)",
        "credit_date": "Fecha del asiento de reversión (opcional, ISO)",
    },
)
async def credit_note(env: Environment, record_id: int, params: dict[str, Any]) -> dict[str, Any]:
    reversal_id = await PurchaseService(env).action_credit_note(
        record_id,
        reason=str(params.get("reason", "")),
        credit_date=params.get("credit_date"),
    )
    return {"credit_note_move_id": reversal_id, "state": "credited"}


@action(
    "purchase.order",
    "action_cancel",
    summary="Cancela una orden sin factura registrada",
)
async def cancel(env: Environment, record_id: int, params: dict[str, Any]) -> dict[str, Any]:
    await PurchaseService(env).action_cancel(record_id)
    return {"state": "cancelled"}


@action(
    "purchase.order",
    "action_receive",
    summary="Recibe la orden: picking de entrada al costo de cada línea",
    params={
        "location_to_id": "Ubicación interna de destino (opcional)",
        "warehouse_id": (
            "Almacén que recibe; basta con esto si el almacén tiene una sola "
            "ubicación interna (opcional)"
        ),
    },
)
async def receive(env: Environment, record_id: int, params: dict[str, Any]) -> dict[str, Any]:
    from modules.stock.fulfillment import receive_purchase

    location = params.get("location_to_id")
    warehouse = params.get("warehouse_id")
    return await receive_purchase(
        env,
        record_id,
        location_to_id=int(location) if location else None,
        warehouse_id=int(warehouse) if warehouse else None,
    )


@action(
    "stock.reorder.rule",
    "action_replenish_buy",
    summary="Crea la orden de compra en borrador que repone la regla hasta su objetivo",
    # Vive en `purchase` y no en `stock` porque crea una orden de compra, y
    # `stock` no puede depender de `purchase` sin invertir la flecha. Queda en
    # borrador con su propio action_confirm: proponer no es comprometer.
)
async def replenish_buy(env: Environment, record_id: int, params: dict[str, Any]) -> dict[str, Any]:
    from modules.stock.reorder import ReorderService
    from modules.stock.replenishment import ZERO, ReplenishError

    service = ReorderService(env)
    rule = await service.rule(record_id)
    if rule["route"] != "buy":
        raise ReplenishError(
            "STOCK_REPLENISH_NO_SOURCE",
            "La regla repone trasladando, no comprando",
            hint="Usa action_replenish, que crea el traslado interno.",
        )
    if not rule["supplier_id"]:
        raise ReplenishError(
            "STOCK_REPLENISH_NO_SOURCE",
            "La regla no declara a qué proveedor comprar",
            hint="Fija supplier_id en la regla.",
        )
    quantity = await service.needed(rule)
    if quantity == ZERO:
        raise ReplenishError(
            "STOCK_REPLENISH_NOT_NEEDED",
            "El stock todavía está sobre el mínimo: no hay nada que comprar",
            hint="La regla se dispara bajo el mínimo, no bajo el máximo.",
        )

    [product] = await RecordSet(env, "product.product").read(
        [rule["product_id"]], fields=["name", "cost"]
    )
    [company] = await RecordSet(env, "res.company").read(
        [rule["company_id"]], fields=["currency_id"]
    )
    journals = await RecordSet(env, "account.journal").search(
        [("company_id", "=", rule["company_id"]), ("journal_type", "=", "purchase")],
        fields=["id"],
        limit=1,
    )
    if not journals["rows"]:
        raise ReplenishError(
            "PURCHASE_NO_JOURNAL",
            "La compañía no tiene diario de compras",
            hint="Crea un account.journal de tipo purchase antes de comprar.",
        )

    order_id = await PurchaseService(env).create_order(
        partner_id=rule["supplier_id"],
        date_order=datetime.now(UTC).date().isoformat(),
        currency_id=company["currency_id"],
        journal_id=journals["rows"][0]["id"],
        company_id=rule["company_id"],
        lines=[
            {
                "name": product["name"],
                "product_id": rule["product_id"],
                "quantity": str(quantity),
                "price_unit": product["cost"] or Decimal("0"),
            }
        ],
        note=f"Reposición automática de la regla {record_id}",
    )
    return {
        "rule_id": record_id,
        "route": "buy",
        "purchase_order_id": order_id,
        "quantity": str(quantity),
        "state": "draft",
    }
