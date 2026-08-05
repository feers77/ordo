"""Acciones del catálogo expuestas a la API."""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from ordo_core.actions import action
from ordo_core.environment import Environment

from modules.product.services import ProductError, VariantService


def _price_by_value(raw: Any) -> dict[int, Decimal] | None:
    """Sobreprecios por valor de atributo, como string decimal.

    Nunca float: la talla XXL que cuesta $1.500 más no puede convertirse en
    $1.499,9999 por pasar por un binario (AGENTS.md §2.3).
    """
    if not raw:
        return None
    if not isinstance(raw, dict):
        raise ProductError(
            "FIELD_INVALID_VALUE",
            "price_by_value debe ser un mapa de id de valor a importe",
            hint='Ejemplo: {"12": "1500.00"} suma $1.500 a las variantes con ese valor.',
        )
    return {int(key): Decimal(str(value)) for key, value in raw.items()}


@action(
    "product.template",
    "action_generate_variants",
    summary=(
        "Crea las variantes que faltan del producto cartesiano de los ejes "
        "declarados; regenerar no duplica"
    ),
    params={
        "price_by_value": (
            "Sobreprecio por valor de atributo, como mapa de id a string decimal (opcional)"
        )
    },
)
async def generate_variants(
    env: Environment, record_id: int, params: dict[str, Any]
) -> dict[str, Any]:
    return await VariantService(env).action_generate_variants(
        record_id, price_by_value=_price_by_value(params.get("price_by_value"))
    )
