"""Reportes del punto de venta: el Z del turno y el histórico de diferencias."""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from ordo_core.environment import Environment
from ordo_core.recordset import RecordSet
from ordo_core.reports import report

from modules.pos.services import PosError

ZERO = Decimal("0")


def _required(params: dict[str, Any], name: str) -> int:
    raw = params.get(name)
    if raw is None:
        raise PosError(
            "REPORT_PARAM_REQUIRED",
            f"El reporte necesita {name}",
            hint=f"Pasa {name} como parámetro.",
        )
    return int(raw)


@report(
    "pos.session_summary",
    summary="El Z del turno: ventas, impuestos, cobros por medio y arqueo",
    params={"session_id": "Turno (obligatorio)"},
)
async def session_summary(env: Environment, params: dict[str, Any]) -> dict[str, Any]:
    session_id = _required(params, "session_id")
    rows = await RecordSet(env, "pos.session").read(
        [session_id],
        fields=[
            "id",
            "name",
            "state",
            "config_id",
            "opening_cash",
            "counted_cash",
            "expected_cash",
            "difference",
            "withdrawals",
        ],
    )
    if not rows:
        raise PosError("POS_SESSION_NOT_FOUND", f"No existe el turno {session_id}")
    session = rows[0]

    orders = await RecordSet(env, "pos.order").search(
        [("session_id", "=", session_id), ("state", "=", "paid")],
        fields=["id", "name", "amount_untaxed", "amount_tax", "amount_total", "refund_of_id"],
        limit=1000,
    )
    sales = [row for row in orders["rows"] if not row["refund_of_id"]]
    refunds = [row for row in orders["rows"] if row["refund_of_id"]]

    def total(items: list[dict[str, Any]], field: str) -> Decimal:
        return sum((row[field] or ZERO for row in items), ZERO)

    by_method: dict[str, Decimal] = {}
    if orders["rows"]:
        order_ids = [row["id"] for row in orders["rows"]]
        payments = await RecordSet(env, "pos.payment").search(
            [("order_id", "in", order_ids)],
            fields=["id", "method_id", "amount"],
            limit=len(order_ids) * 10,
        )
        method_ids = sorted({row["method_id"] for row in payments["rows"]})
        if method_ids:
            methods = await RecordSet(env, "pos.payment.method").search(
                [("id", "in", method_ids)], fields=["id", "code"], limit=len(method_ids)
            )
            code_by_id = {row["id"]: row["code"] for row in methods["rows"]}
            for row in payments["rows"]:
                code = code_by_id.get(row["method_id"], str(row["method_id"]))
                by_method[code] = by_method.get(code, ZERO) + Decimal(str(row["amount"]))

    return {
        "report": "pos.session_summary",
        "session_id": session_id,
        "name": session["name"],
        "state": session["state"],
        "tickets": len(sales),
        "refunds": len(refunds),
        "net_untaxed": str(total(sales, "amount_untaxed") + total(refunds, "amount_untaxed")),
        "net_tax": str(total(sales, "amount_tax") + total(refunds, "amount_tax")),
        "net_total": str(total(sales, "amount_total") + total(refunds, "amount_total")),
        # Los cobros de las devoluciones vienen en negativo: lo que se cobró
        # por cada medio ya está neto de lo que se devolvió por ese mismo medio.
        "by_method": {code: str(amount) for code, amount in sorted(by_method.items())},
        "opening_cash": str(session["opening_cash"] or ZERO),
        "withdrawals": str(session["withdrawals"] or ZERO),
        "expected_cash": None
        if session["expected_cash"] is None
        else str(session["expected_cash"]),
        "counted_cash": None if session["counted_cash"] is None else str(session["counted_cash"]),
        "difference": None if session["difference"] is None else str(session["difference"]),
    }


@report(
    "pos.cash_differences",
    summary="Histórico de diferencias de arqueo por caja: donde se ve el robo hormiga",
    params={"company_id": "Compañía (obligatorio)"},
)
async def cash_differences(env: Environment, params: dict[str, Any]) -> dict[str, Any]:
    company_id = _required(params, "company_id")
    sessions = await RecordSet(env, "pos.session").search(
        [("company_id", "=", company_id), ("state", "=", "closed")],
        fields=["id", "name", "config_id", "closed_at", "difference", "note"],
        limit=1000,
    )
    configs = await RecordSet(env, "pos.config").search(
        [("company_id", "=", company_id)], fields=["id", "name"], limit=100
    )
    name_by_config = {row["id"]: row["name"] for row in configs["rows"]}

    rows = []
    total = ZERO
    shortfalls = ZERO
    for session in sessions["rows"]:
        gap = session["difference"] or ZERO
        total += gap
        if gap < ZERO:
            shortfalls += gap
        rows.append(
            {
                "session_id": session["id"],
                "name": session["name"],
                "register": name_by_config.get(session["config_id"]),
                "closed_at": None if session["closed_at"] is None else str(session["closed_at"]),
                "difference": str(gap),
                "note": session["note"],
            }
        )
    return {
        "report": "pos.cash_differences",
        # Se listan también los turnos cuadrados: la ausencia de diferencia es
        # dato, y una caja que nunca descuadra ni en un peso también dice algo.
        "rows": sorted(rows, key=lambda row: row["session_id"]),
        "sessions": len(rows),
        "net_difference": str(total),
        "total_shortfalls": str(shortfalls),
    }
