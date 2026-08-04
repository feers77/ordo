"""Acciones contables expuestas a la API (registro de F2/actions).

Contabilizar exige aprobación humana: es el punto donde un borrador se
vuelve historia legal con número consumido (AGENTS.md §6).
"""

from __future__ import annotations

from typing import Any

from ordo_core.actions import action
from ordo_core.environment import Environment

from modules.account.services import AccountingService


@action(
    "account.move",
    "action_post",
    summary="Contabiliza el asiento y le asigna su número legal",
    requires_approval=True,
)
async def post(env: Environment, record_id: int, params: dict[str, Any]) -> dict[str, Any]:
    return {"name": await AccountingService(env).action_post(record_id)}


@action(
    "account.move",
    "action_reverse",
    summary="Crea el asiento inverso; el original queda intacto",
    requires_approval=True,
    params={"reversal_date": "Fecha del asiento de reversión (opcional, ISO)"},
)
async def reverse(env: Environment, record_id: int, params: dict[str, Any]) -> dict[str, Any]:
    reversal_id = await AccountingService(env).action_reverse(
        record_id, params.get("reversal_date")
    )
    return {"reversal_id": reversal_id}


@action(
    "account.move",
    "action_cancel",
    summary="Anula un asiento en borrador (uno contabilizado se revierte)",
)
async def cancel(env: Environment, record_id: int, params: dict[str, Any]) -> dict[str, Any]:
    await AccountingService(env).action_cancel(record_id)
    return {"state": "cancel"}


@action(
    "account.payment",
    "action_post",
    summary="Contabiliza el pago contra el banco y la cuenta del tercero",
    requires_approval=True,
)
async def post_payment(env: Environment, record_id: int, params: dict[str, Any]) -> dict[str, Any]:
    from modules.account.payments import PaymentService

    return {"move_id": await PaymentService(env).action_post(record_id)}


@action(
    "account.payment",
    "action_cancel",
    summary="Anula un pago en borrador (uno contabilizado se revierte)",
)
async def cancel_payment(
    env: Environment, record_id: int, params: dict[str, Any]
) -> dict[str, Any]:
    from modules.account.payments import PaymentService

    await PaymentService(env).action_cancel(record_id)
    return {"state": "cancelled"}


@action(
    "account.move.line",
    "action_reconcile",
    summary="Concilia esta partida con otras de la misma cuenta; deben saldar en cero",
    params={"with_line_ids": "Ids de las otras partidas del grupo (lista)"},
)
async def reconcile_lines(
    env: Environment, record_id: int, params: dict[str, Any]
) -> dict[str, Any]:
    from modules.account.reconcile import ReconcileService

    others = params.get("with_line_ids") or []
    group_id = await ReconcileService(env).reconcile([record_id, *others])
    return {"reconcile_id": group_id}


@action(
    "account.reconcile",
    "action_unreconcile",
    summary="Deshace el grupo de conciliación y libera sus partidas",
    requires_approval=True,
)
async def unreconcile_group(
    env: Environment, record_id: int, params: dict[str, Any]
) -> dict[str, Any]:
    from modules.account.reconcile import ReconcileService

    released = await ReconcileService(env).unreconcile(record_id)
    return {"released": released}


@action(
    "account.bank.statement",
    "action_auto_match",
    summary="Empareja movimientos del extracto contra partidas del banco por importe exacto",
)
async def auto_match_statement(
    env: Environment, record_id: int, params: dict[str, Any]
) -> dict[str, Any]:
    from modules.account.statements import StatementService

    return await StatementService(env).auto_match(record_id)


@action(
    "account.bank.statement",
    "action_validate",
    summary="Valida el extracto: cuadrado contra saldos y con todo emparejado",
    requires_approval=True,
)
async def validate_statement(
    env: Environment, record_id: int, params: dict[str, Any]
) -> dict[str, Any]:
    from modules.account.statements import StatementService

    await StatementService(env).action_validate(record_id)
    return {"state": "validated"}
