"""Reportes de inventario: existencias y su valor, sin números inventados."""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from ordo_core.environment import Environment
from ordo_core.recordset import RecordSet
from ordo_core.reports import report

from modules.account.services import AccountingError
from modules.stock.replenishment import suggested_quantity
from modules.stock.services import StockService

ZERO = Decimal("0")


def _company(params: dict[str, Any]) -> int:
    raw = params.get("company_id")
    if raw is None:
        raise AccountingError(
            "REPORT_PARAM_REQUIRED",
            "El reporte necesita company_id",
            hint="Pasa company_id como parámetro.",
        )
    return int(raw)


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
    summary="Productos bajo su mínimo, agrupados por modelo y desglosados por variante",
    params={"company_id": "Compañía (obligatorio)"},
)
async def reorder_alerts(env: Environment, params: dict[str, Any]) -> dict[str, Any]:
    """ "Quedan 2 poleras" no sirve; "quedan 0 en talla M" sí.

    Por eso las alertas se agrupan por modelo y se desglosan por variante: en
    moda, el modelo con stock suficiente en total puede estar agotado justo en
    la talla que se vende.
    """
    company_id = _company(params)
    service = StockService(env)
    rules = await RecordSet(env, "stock.reorder.rule").search(
        [("company_id", "=", company_id)],
        fields=[
            "id",
            "product_id",
            "location_id",
            "min_quantity",
            "max_quantity",
            "route",
            "source_location_id",
            "multiple_quantity",
        ],
        limit=1000,
    )
    products = RecordSet(env, "product.product")
    alerts = []
    for rule in rules["rows"]:
        available = await service.on_hand(rule["product_id"], rule["location_id"])
        multiple = rule["multiple_quantity"]
        needed = suggested_quantity(
            available,
            Decimal(rule["min_quantity"]),
            Decimal(rule["max_quantity"]),
            multiple=Decimal(multiple) if multiple else None,
        )
        if needed == ZERO:
            continue
        [product] = await products.read(
            [rule["product_id"]], fields=["name", "default_code", "template_id", "variant_label"]
        )
        at_source = None
        can_replenish = rule["route"] == "internal" and bool(rule["source_location_id"])
        if can_replenish:
            at_source = await service.on_hand(rule["product_id"], rule["source_location_id"])
            can_replenish = at_source >= needed
        alerts.append(
            {
                "rule_id": rule["id"],
                "product_id": rule["product_id"],
                "template_id": product["template_id"],
                "name": product["name"],
                "variant_label": product["variant_label"],
                "default_code": product["default_code"],
                "location_id": rule["location_id"],
                "on_hand": str(available),
                "min_quantity": rule["min_quantity"],
                "suggested_quantity": str(needed),
                "route": rule["route"],
                "source_location_id": rule["source_location_id"],
                "on_hand_source": None if at_source is None else str(at_source),
                # Falso significa que la acción fallaría: o no hay origen, o el
                # origen tampoco tiene. Decirlo aquí evita el intento inútil.
                "can_replenish": can_replenish,
                "suggested_action": (
                    "action_replenish" if rule["route"] == "internal" else "action_replenish_buy"
                ),
            }
        )

    by_template: dict[Any, dict[str, Any]] = {}
    for alert in alerts:
        key = alert["template_id"] or f"p{alert['product_id']}"
        group = by_template.setdefault(
            key,
            {
                "template_id": alert["template_id"],
                "name": alert["name"],
                "variants": [],
                "total_suggested": ZERO,
            },
        )
        group["variants"].append(alert)
        group["total_suggested"] += Decimal(alert["suggested_quantity"])

    groups = [
        {
            "template_id": group["template_id"],
            "name": group["name"],
            "variants": sorted(
                group["variants"], key=lambda row: row["variant_label"] or row["name"]
            ),
            "total_suggested": str(group["total_suggested"]),
        }
        for group in by_template.values()
    ]
    return {
        "report": "stock.reorder_alerts",
        "alerts": sorted(alerts, key=lambda row: (row["name"], row["variant_label"] or "")),
        "by_template": sorted(groups, key=lambda row: row["name"]),
        "count": len(alerts),
    }


@report(
    "stock.replenishment_plan",
    summary="El plan de reposición completo de una ubicación, listo para ejecutar",
    params={
        "company_id": "Compañía (obligatorio)",
        "location_id": "Ubicación a reponer; si falta, todas",
    },
)
async def replenishment_plan(env: Environment, params: dict[str, Any]) -> dict[str, Any]:
    """Todo lo que hay que reponer en una llamada.

    Existe para que reponer una tienda no sean cincuenta consultas: el agente
    pide el plan, lo revisa con la dueña y ejecuta las acciones que le indica
    cada línea.
    """
    alerts = await reorder_alerts(env, params)
    location_id = params.get("location_id")
    rows = alerts["alerts"]
    if location_id is not None:
        rows = [row for row in rows if row["location_id"] == int(location_id)]
    ready = [row for row in rows if row["can_replenish"]]
    blocked = [row for row in rows if not row["can_replenish"]]
    return {
        "report": "stock.replenishment_plan",
        "location_id": None if location_id is None else int(location_id),
        "ready": ready,
        # Se listan aparte y no se ocultan: una línea bloqueada es trabajo que
        # alguien tiene que resolver, no ruido que convenga esconder.
        "blocked": blocked,
        "count": len(rows),
    }
