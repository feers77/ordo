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
