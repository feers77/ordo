"""Reportes de inventario: existencias y su valor, sin números inventados."""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from ordo_core.environment import Environment
from ordo_core.recordset import RecordSet
from ordo_core.reports import report

from modules.account.services import AccountingError
from modules.stock.services import StockService

ZERO = Decimal("0")


@report(
    "stock.on_hand",
    summary="Existencias y valor por producto en las ubicaciones internas",
    params={"company_id": "Compañía (obligatorio)"},
)
async def on_hand(env: Environment, params: dict[str, Any]) -> dict[str, Any]:
    raw = params.get("company_id")
    if raw is None:
        raise AccountingError(
            "REPORT_PARAM_REQUIRED",
            "El reporte necesita company_id",
            hint="Pasa company_id como parámetro.",
        )
    company_id = int(raw)
    service = StockService(env)
    products = await RecordSet(env, "product.product").search(
        [("company_id", "=", company_id), ("product_type", "=", "consu")],
        fields=["id", "name", "default_code", "cost"],
        limit=1000,
    )
    rows = []
    total_value = ZERO
    for product in products["rows"]:
        quantity = await service.on_hand_company(product["id"], company_id)
        if quantity == ZERO:
            continue
        cost = product["cost"] or ZERO
        value = quantity * cost
        total_value += value
        rows.append(
            {
                "product_id": product["id"],
                "name": product["name"],
                "default_code": product["default_code"],
                "quantity": str(quantity),
                "average_cost": str(cost),
                "value": str(value),
            }
        )
    return {
        "report": "stock.on_hand",
        "rows": sorted(rows, key=lambda row: row["name"]),
        "total_value": str(total_value),
    }
