"""Cuánto reponer: aritmética pura, sin base de datos.

Separado del servicio porque es donde se equivocan los sistemas de reposición:
piden de menos y la tienda se queda sin talla M el sábado, o piden de más y la
plata queda parada en bodega. Se prueba con property-based testing.
"""

from __future__ import annotations

from decimal import ROUND_CEILING, Decimal

from ordo_core.errors import KernelError

ZERO = Decimal("0")


class ReplenishError(KernelError):
    """Error de reposición con código estable."""


def validate_range(minimum: Decimal, maximum: Decimal) -> None:
    if minimum < ZERO or maximum < ZERO:
        raise ReplenishError(
            "STOCK_RULE_INVALID_RANGE",
            "Los niveles de una regla no pueden ser negativos",
            hint="min_quantity y max_quantity son cantidades, no ajustes.",
        )
    if minimum > maximum:
        raise ReplenishError(
            "STOCK_RULE_INVALID_RANGE",
            f"El mínimo ({minimum}) no puede superar al máximo ({maximum})",
            hint="Reponer hasta un objetivo menor que el disparador no tiene sentido.",
        )


def suggested_quantity(
    on_hand: Decimal,
    minimum: Decimal,
    maximum: Decimal,
    *,
    multiple: Decimal | None = None,
) -> Decimal:
    """Lo que hay que traer para volver al objetivo.

    Cero si el stock todavía está sobre el mínimo: la regla se dispara por
    debajo del mínimo, no por debajo del máximo. Reponer en cada venta llenaría
    la bodega de traslados de una unidad.

    El múltiplo redondea **hacia arriba**: si el proveedor vende cajas de 12 y
    faltan 13, se piden 24, no 12 — quedarse corto es el error caro.
    """
    validate_range(minimum, maximum)
    if on_hand >= minimum:
        return ZERO
    needed = maximum - on_hand
    if needed <= ZERO:
        return ZERO
    if multiple is None or multiple <= ZERO:
        return needed
    return (needed / multiple).quantize(Decimal("1"), rounding=ROUND_CEILING) * multiple
