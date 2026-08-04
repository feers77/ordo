"""Matemática pura del costo promedio.

Separada del servicio para probarla con hypothesis: el promedio nunca puede
salirse del rango de los precios que entraron, y el valor total siempre es
cantidad x promedio (redondeo declarado mediante).
"""

from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal

from ordo_core.errors import KernelError

ZERO = Decimal("0")


class ValuationError(KernelError):
    """Error de valorización con código estable."""


def money(value: Decimal, decimals: int = 2) -> Decimal:
    exponent = Decimal(1).scaleb(-decimals)
    return value.quantize(exponent, rounding=ROUND_HALF_UP)


def new_average(
    on_hand: Decimal,
    current_avg: Decimal,
    qty_in: Decimal,
    unit_cost: Decimal,
    *,
    decimals: int = 2,
) -> Decimal:
    """Promedio ponderado tras una entrada.

    Con stock negativo o cero el promedio ES el costo de la entrada: promediar
    contra un fantasma produce costos absurdos.
    """
    if qty_in <= ZERO:
        raise ValuationError(
            "STOCK_INVALID_QUANTITY", "Una entrada valorizada exige cantidad positiva"
        )
    if unit_cost < ZERO:
        raise ValuationError("STOCK_NEGATIVE_COST", "El costo unitario no puede ser negativo")
    if on_hand <= ZERO:
        return money(unit_cost, decimals)
    total_value = on_hand * current_avg + qty_in * unit_cost
    return money(total_value / (on_hand + qty_in), decimals)
