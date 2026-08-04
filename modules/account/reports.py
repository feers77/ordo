"""Reportes financieros base: balance de comprobación, resultados y balance.

Los importes viajan como string decimal (contrato de la API). Cada reporte
declara su propio control de integridad: un balance de comprobación cuyo
debe no iguala al haber es una alarma, no un reporte.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from ordo_core.environment import Environment
from ordo_core.recordset import RecordSet
from ordo_core.reports import report

from modules.account.services import AccountingError

ZERO = Decimal("0")

# Cuentas cuyo saldo natural va al haber: se muestran con signo positivo
# cuando acreditan.
CREDIT_NATURE = {"liability", "equity", "income"}


def _company_id(params: dict[str, Any]) -> int:
    raw = params.get("company_id")
    if raw is None:
        raise AccountingError(
            "REPORT_PARAM_REQUIRED",
            "El reporte necesita company_id",
            hint="Pasa company_id como parámetro.",
        )
    return int(raw)


async def _posted_lines(
    env: Environment,
    company_id: int,
    *,
    date_from: str | None = None,
    date_to: str | None = None,
) -> list[dict[str, Any]]:
    domain: list[Any] = [
        ("company_id", "=", company_id),
        ("move_id.state", "=", "posted"),
    ]
    if date_from:
        domain.append(("move_id.date", ">=", date_from))
    if date_to:
        domain.append(("move_id.date", "<=", date_to))

    lines = RecordSet(env, "account.move.line")
    rows: list[dict[str, Any]] = []
    cursor: str | None = None
    while True:
        page = await lines.search(
            domain,
            fields=["id", "account_id", "debit", "credit"],
            limit=500,
            cursor=cursor,
            active_test=False,
        )
        rows.extend(page["rows"])
        cursor = page.get("next_cursor")
        if not cursor:
            break
    return rows


async def _accounts_of(env: Environment, account_ids: set[int]) -> dict[int, dict[str, Any]]:
    if not account_ids:
        return {}
    rows = await RecordSet(env, "account.account").read(
        sorted(account_ids), fields=["id", "code", "name", "account_type"]
    )
    return {row["id"]: row for row in rows}


async def _balances_by_account(
    env: Environment,
    company_id: int,
    *,
    date_from: str | None = None,
    date_to: str | None = None,
) -> list[dict[str, Any]]:
    lines = await _posted_lines(env, company_id, date_from=date_from, date_to=date_to)
    debit_by_account: dict[int, Decimal] = {}
    credit_by_account: dict[int, Decimal] = {}
    for line in lines:
        debit_by_account[line["account_id"]] = (
            debit_by_account.get(line["account_id"], ZERO) + line["debit"]
        )
        credit_by_account[line["account_id"]] = (
            credit_by_account.get(line["account_id"], ZERO) + line["credit"]
        )
    accounts = await _accounts_of(env, set(debit_by_account))
    rows = []
    for account_id, account in accounts.items():
        debit = debit_by_account.get(account_id, ZERO)
        credit = credit_by_account.get(account_id, ZERO)
        rows.append(
            {
                "account_id": account_id,
                "code": account["code"],
                "name": account["name"],
                "account_type": account["account_type"],
                "debit": debit,
                "credit": credit,
                "balance": debit - credit,
            }
        )
    return sorted(rows, key=lambda row: row["code"])


def _stringify(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {key: str(value) if isinstance(value, Decimal) else value for key, value in row.items()}
        for row in rows
    ]


@report(
    "account.trial_balance",
    summary="Balance de comprobación: sumas y saldos por cuenta",
    params={
        "company_id": "Compañía (obligatorio)",
        "date_from": "Desde, ISO (opcional)",
        "date_to": "Hasta, ISO (opcional)",
    },
)
async def trial_balance(env: Environment, params: dict[str, Any]) -> dict[str, Any]:
    company_id = _company_id(params)
    rows = await _balances_by_account(
        env, company_id, date_from=params.get("date_from"), date_to=params.get("date_to")
    )
    total_debit = sum((row["debit"] for row in rows), ZERO)
    total_credit = sum((row["credit"] for row in rows), ZERO)
    return {
        "report": "account.trial_balance",
        "rows": _stringify(rows),
        "total_debit": str(total_debit),
        "total_credit": str(total_credit),
        # Si esto es falso hay un bug en el kernel, no en los datos: la
        # partida doble se valida al contabilizar.
        "balanced": total_debit == total_credit,
    }


@report(
    "account.income_statement",
    summary="Estado de resultados: ingresos menos gastos del período",
    params={
        "company_id": "Compañía (obligatorio)",
        "date_from": "Desde, ISO (opcional)",
        "date_to": "Hasta, ISO (opcional)",
    },
)
async def income_statement(env: Environment, params: dict[str, Any]) -> dict[str, Any]:
    company_id = _company_id(params)
    rows = await _balances_by_account(
        env, company_id, date_from=params.get("date_from"), date_to=params.get("date_to")
    )
    income_rows = [row for row in rows if row["account_type"] == "income"]
    expense_rows = [row for row in rows if row["account_type"] == "expense"]
    total_income = sum((row["credit"] - row["debit"] for row in income_rows), ZERO)
    total_expense = sum((row["debit"] - row["credit"] for row in expense_rows), ZERO)
    return {
        "report": "account.income_statement",
        "income": _stringify(income_rows),
        "expense": _stringify(expense_rows),
        "total_income": str(total_income),
        "total_expense": str(total_expense),
        "result": str(total_income - total_expense),
    }


@report(
    "account.balance_sheet",
    summary="Balance general a una fecha, con el resultado del ejercicio incluido",
    params={
        "company_id": "Compañía (obligatorio)",
        "date_to": "Fecha de corte, ISO (opcional; por defecto todo lo contabilizado)",
    },
)
async def balance_sheet(env: Environment, params: dict[str, Any]) -> dict[str, Any]:
    company_id = _company_id(params)
    rows = await _balances_by_account(env, company_id, date_to=params.get("date_to"))

    def total(kind: str) -> Decimal:
        selected = (row for row in rows if row["account_type"] == kind)
        if kind in CREDIT_NATURE:
            return sum((row["credit"] - row["debit"] for row in selected), ZERO)
        return sum((row["debit"] - row["credit"] for row in selected), ZERO)

    assets = total("asset")
    liabilities = total("liability")
    equity = total("equity")
    result = total("income") - total("expense")
    return {
        "report": "account.balance_sheet",
        "assets": _stringify([row for row in rows if row["account_type"] == "asset"]),
        "liabilities": _stringify([row for row in rows if row["account_type"] == "liability"]),
        "equity": _stringify([row for row in rows if row["account_type"] == "equity"]),
        "total_assets": str(assets),
        "total_liabilities": str(liabilities),
        "total_equity": str(equity),
        "period_result": str(result),
        # Activo = Pasivo + Patrimonio + Resultado: si no cuadra, alarma.
        "balanced": assets == liabilities + equity + result,
    }
