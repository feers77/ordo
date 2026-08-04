"""Acciones de webhooks expuestas a la API."""

from __future__ import annotations

from typing import Any

from ordo_core.actions import action
from ordo_core.environment import Environment
from ordo_core.recordset import RecordSet

from modules.webhook.service import WebhookError, WebhookService, generate_secret


@action(
    "webhook.subscription",
    "action_suspend",
    summary="Suspende la suscripción: deja de recibir eventos sin perder su historia",
)
async def suspend(env: Environment, record_id: int, params: dict[str, Any]) -> dict[str, Any]:
    await WebhookService(env).action_suspend(record_id)
    return {"state": "suspended"}


@action(
    "webhook.subscription",
    "action_resume",
    summary="Reanuda la suscripción y pone en cero los fallos acumulados",
)
async def resume(env: Environment, record_id: int, params: dict[str, Any]) -> dict[str, Any]:
    await WebhookService(env).action_resume(record_id)
    return {"state": "active", "failure_count": 0}


@action(
    "webhook.subscription",
    "action_rotate_secret",
    summary="Genera un secreto nuevo y lo devuelve una sola vez; invalida el anterior",
    requires_approval=True,
)
async def rotate_secret(env: Environment, record_id: int, params: dict[str, Any]) -> dict[str, Any]:
    """Rotar el secreto rompe toda verificación en curso: exige aprobación."""
    subscriptions = RecordSet(env, "webhook.subscription")
    if not await subscriptions.read([record_id], fields=["id"]):
        raise WebhookError(
            "WEBHOOK_NOT_FOUND",
            f"No existe la suscripción {record_id}",
            hint="Lista webhook.subscription para ver las disponibles.",
        )
    secret = generate_secret()
    await subscriptions.write([record_id], {"secret": secret})
    return {"secret": secret}
