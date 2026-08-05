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


@report(
    "stock.variant_matrix",
    summary="Existencias y valor por variante de un modelo: qué talla se agotó",
    params={
        "template_id": "Modelo de producto (obligatorio)",
        "company_id": "Compañía (obligatorio)",
    },
)
async def variant_matrix(env: Environment, params: dict[str, Any]) -> dict[str, Any]:
    """El reporte de una tienda de ropa.

    Vive en `stock` y no en `product` porque necesita existencias, y la flecha
    de dependencia va de inventario hacia catálogo, nunca al revés.
    """
    for name in ("template_id", "company_id"):
        if params.get(name) is None:
            raise AccountingError(
                "REPORT_PARAM_REQUIRED",
                f"El reporte necesita {name}",
                hint=f"Pasa {name} como parámetro.",
            )
    template_id = int(params["template_id"])
    company_id = int(params["company_id"])

    variants = await RecordSet(env, "product.product").search(
        [("template_id", "=", template_id), ("company_id", "=", company_id)],
        fields=["id", "name", "default_code", "variant_label", "cost"],
        limit=1000,
    )
    variant_ids = [row["id"] for row in variants["rows"]]
    if not variant_ids:
        return {
            "report": "stock.variant_matrix",
            "template_id": template_id,
            "axes": [],
            "rows": [],
            "total_quantity": "0",
            "total_value": str(ZERO),
        }

    memberships = await RecordSet(env, "product.variant.value").search(
        [("product_id", "in", variant_ids)],
        fields=["product_id", "attribute_id", "value_id"],
        limit=len(variant_ids) * 10,
    )
    attribute_ids = sorted({row["attribute_id"] for row in memberships["rows"]})
    value_ids = sorted({row["value_id"] for row in memberships["rows"]})
    attributes = await RecordSet(env, "product.attribute").search(
        [("id", "in", attribute_ids)], fields=["id", "name", "sequence"], limit=50
    )
    values = await RecordSet(env, "product.attribute.value").search(
        [("id", "in", value_ids)], fields=["id", "name", "attribute_id", "sequence"], limit=500
    )
    value_by_id = {row["id"]: row for row in values["rows"]}
    axes = [
        {
            "attribute_id": attribute["id"],
            "name": attribute["name"],
            "values": [
                {"value_id": value["id"], "name": value["name"]}
                for value in sorted(
                    (v for v in values["rows"] if v["attribute_id"] == attribute["id"]),
                    key=lambda item: (item["sequence"] or 0, item["id"]),
                )
            ],
        }
        for attribute in sorted(
            attributes["rows"], key=lambda item: (item["sequence"] or 0, item["id"])
        )
    ]

    combos: dict[int, dict[str, str]] = {}
    for row in memberships["rows"]:
        value = value_by_id.get(row["value_id"])
        if value is None:
            continue
        combos.setdefault(row["product_id"], {})[str(row["attribute_id"])] = value["name"]

    service = StockService(env)
    rows = []
    total_quantity = ZERO
    total_value = ZERO
    for variant in variants["rows"]:
        quantity = await service.on_hand_company(variant["id"], company_id)
        cost = variant["cost"] or ZERO
        value = quantity * cost
        total_quantity += quantity
        total_value += value
        rows.append(
            {
                "product_id": variant["id"],
                "name": variant["name"],
                "default_code": variant["default_code"],
                "variant_label": variant["variant_label"],
                "values": combos.get(variant["id"], {}),
                "quantity": str(quantity),
                "average_cost": str(cost),
                "value": str(value),
            }
        )
    return {
        "report": "stock.variant_matrix",
        "template_id": template_id,
        "axes": axes,
        # Se listan también las variantes en cero: en moda, la talla agotada es
        # justo la fila que hay que ver.
        "rows": sorted(rows, key=lambda row: row["variant_label"] or ""),
        "total_quantity": str(total_quantity),
        "total_value": str(total_value),
    }


@report(
    "stock.reorder_alerts",
    summary="Productos bajo su mínimo con la cantidad sugerida a reponer",
    params={"company_id": "Compañía (obligatorio)"},
)
async def reorder_alerts(env: Environment, params: dict[str, Any]) -> dict[str, Any]:
    raw = params.get("company_id")
    if raw is None:
        raise AccountingError(
            "REPORT_PARAM_REQUIRED",
            "El reporte necesita company_id",
            hint="Pasa company_id como parámetro.",
        )
    company_id = int(raw)
    service = StockService(env)
    rules = await RecordSet(env, "stock.reorder.rule").search(
        [("company_id", "=", company_id)],
        fields=["id", "product_id", "location_id", "min_quantity", "max_quantity"],
        limit=1000,
    )
    products = RecordSet(env, "product.product")
    alerts = []
    for rule in rules["rows"]:
        available = await service.on_hand(rule["product_id"], rule["location_id"])
        minimum = Decimal(rule["min_quantity"])
        if available >= minimum:
            continue
        [product] = await products.read([rule["product_id"]], fields=["name", "default_code"])
        alerts.append(
            {
                "product_id": rule["product_id"],
                "name": product["name"],
                "default_code": product["default_code"],
                "location_id": rule["location_id"],
                "on_hand": str(available),
                "min_quantity": rule["min_quantity"],
                "suggested_quantity": str(Decimal(rule["max_quantity"]) - available),
            }
        )
    return {
        "report": "stock.reorder_alerts",
        "alerts": sorted(alerts, key=lambda row: row["name"]),
        "count": len(alerts),
    }
