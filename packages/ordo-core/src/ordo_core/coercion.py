"""Conversión de valores entrantes según el tipo del campo.

Un agente habla JSON: las fechas llegan como string ISO-8601 y los importes
como string decimal, que es justamente lo que exige la convención de la API.
El kernel las convierte en un solo lugar, para que escribir y filtrar acepten
exactamente lo mismo.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any

from ordo_core.errors import KernelError
from ordo_core.fields import Field


def parse_temporal(field_type: str, value: str, where: str) -> date | datetime:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise KernelError(
            "FIELD_INVALID_VALUE",
            f"{where} espera una fecha ISO-8601, llegó {value!r}",
            hint="Usa 2026-08-04 para fechas y 2026-08-04T12:00:00Z para instantes.",
        ) from exc
    if field_type == "date":
        return parsed.date()
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def parse_decimal(value: Any, where: str) -> Decimal:
    if isinstance(value, float):
        raise KernelError(
            "FIELD_INVALID_VALUE",
            f"{where} es monetario: usa Decimal o string decimal, nunca float",
            hint="Los float pierden precisión en importes.",
        )
    if isinstance(value, Decimal):
        return value
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise KernelError(
            "FIELD_INVALID_VALUE", f"{where} no es un importe válido: {value!r}"
        ) from exc


def coerce_query_value(field: Field, value: Any, where: str) -> Any:
    """Adapta un valor de dominio al tipo real de la columna."""
    if value is None:
        return None
    if isinstance(value, list | tuple | set):
        return [coerce_query_value(field, item, where) for item in value]
    if field.field_type in {"date", "datetime"} and isinstance(value, str):
        return parse_temporal(field.field_type, value, where)
    if field.field_type == "monetary" and not isinstance(value, Decimal):
        return parse_decimal(value, where)
    return value
