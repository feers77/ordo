"""Acciones del punto de venta expuestas a la API."""

from __future__ import annotations

from decimal import Decimal, InvalidOperation
from typing import Any

from ordo_core.actions import action
from ordo_core.environment import Environment

from modules.pos.services import PosError, PosSessionService


def _decimal(params: dict[str, Any], name: str, *, required: bool = False) -> Decimal:
    """Importes siempre como string decimal, nunca float (AGENTS.md §2.3)."""
    raw = params.get(name)
    if raw is None or raw == "":
        if required:
            raise PosError(
                "POS_COUNTED_CASH_REQUIRED",
                f"Falta el parámetro {name}",
                hint='Los importes van como string decimal, por ejemplo "213500.00".',
            )
        return Decimal("0")
    try:
        return Decimal(str(raw))
    except InvalidOperation as exc:
        raise PosError(
            "FIELD_INVALID_VALUE",
            f"'{raw}' no es un importe válido para {name}",
            hint='Usa string decimal, por ejemplo "50000.00".',
        ) from exc


@action(
    "pos.session",
    "action_open",
    summary="Abre el turno con su fondo de caja declarado y le asigna número",
    params={"opening_cash": "Fondo de caja inicial, string decimal (por defecto 0)"},
)
async def open_session(env: Environment, record_id: int, params: dict[str, Any]) -> dict[str, Any]:
    number = await PosSessionService(env).action_open(
        record_id, opening_cash=_decimal(params, "opening_cash")
    )
    return {"session_id": record_id, "name": number, "state": "opened"}


@action(
    "pos.session",
    "action_close_register",
    summary="Cierra el turno a ventas nuevas, antes de contar el efectivo",
)
async def close_register(
    env: Environment, record_id: int, params: dict[str, Any]
) -> dict[str, Any]:
    return await PosSessionService(env).action_close_register(record_id)


@action(
    "pos.session",
    "action_close",
    summary="Arquea el turno: calcula el esperado, asienta la diferencia y lo cierra",
    # La diferencia de caja es la señal de robo hormiga. Que la persona
    # responsable la vea y la autorice es justamente el control, no un trámite.
    requires_approval=True,
    params={
        "counted_cash": "Efectivo físicamente contado al cierre, string decimal (obligatorio)",
        "withdrawals": "Efectivo retirado del cajón durante el turno, string decimal",
        "note": "Explicación de la diferencia, si la hay",
    },
)
async def close_session(env: Environment, record_id: int, params: dict[str, Any]) -> dict[str, Any]:
    return await PosSessionService(env).action_close(
        record_id,
        counted_cash=_decimal(params, "counted_cash", required=True),
        withdrawals=_decimal(params, "withdrawals"),
        note=str(params.get("note") or ""),
    )
