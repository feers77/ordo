"""Aritmética del arqueo: funciones puras, en Decimal, sin base de datos.

El arqueo es donde una tienda detecta el robo hormiga, y también donde un
redondeo mal puesto lo esconde. Por eso vive aparte y se prueba con
property-based testing.
"""

from __future__ import annotations

from collections.abc import Iterable
from decimal import Decimal

from ordo_core.errors import KernelError

ZERO = Decimal("0")


class CashError(KernelError):
    """Error de caja con código estable."""


def money(value: Decimal, decimals: int = 2) -> Decimal:
    quantum = Decimal(1).scaleb(-decimals)
    return value.quantize(quantum)


def expected_cash(
    *,
    opening: Decimal,
    cash_received: Iterable[Decimal],
    change_given: Iterable[Decimal],
    withdrawals: Decimal = ZERO,
    decimals: int = 2,
) -> Decimal:
    """Lo que debería haber en el cajón al cerrar.

    Fondo inicial, más lo cobrado en efectivo, menos el vuelto entregado, menos
    lo retirado. Los cobros con tarjeta no entran: no pasan por el cajón.
    """
    received = sum(cash_received, ZERO)
    change = sum(change_given, ZERO)
    return money(opening + received - change - withdrawals, decimals)


def difference(counted: Decimal, expected: Decimal, decimals: int = 2) -> Decimal:
    """Contado menos esperado: negativo es faltante, positivo sobrante."""
    return money(counted - expected, decimals)


def validate_payments(
    total: Decimal,
    payments: list[dict[str, Decimal | str]],
    *,
    decimals: int = 2,
) -> Decimal:
    """Comprueba que los cobros cubran el ticket y devuelve el vuelto.

    El vuelto solo existe en efectivo: dar "vuelto" de una tarjeta es devolver
    plata que no entró al cajón, y es exactamente la forma en que una caja se
    desangra sin que el arqueo lo note.
    """
    if not payments:
        raise CashError(
            "POS_PAYMENT_INSUFFICIENT",
            "El ticket no tiene cobros registrados",
            hint="Agrega al menos un pos.payment antes de validar el ticket.",
        )
    collected = ZERO
    cash_collected = ZERO
    for payment in payments:
        amount = Decimal(str(payment["amount"]))
        if amount <= ZERO:
            raise CashError(
                "POS_PAYMENT_INSUFFICIENT",
                f"Un cobro de {amount} no es un cobro",
                hint="Los importes cobrados son positivos; una devolución es otro documento.",
            )
        collected += amount
        if payment.get("method_type") == "cash":
            cash_collected += amount

    total = money(total, decimals)
    collected = money(collected, decimals)
    if collected < total:
        raise CashError(
            "POS_PAYMENT_INSUFFICIENT",
            f"Los cobros suman {collected} y el ticket es de {total}",
            hint="Agrega un cobro por la diferencia antes de validar.",
        )
    change = money(collected - total, decimals)
    if change > ZERO and change > money(cash_collected, decimals):
        raise CashError(
            "POS_CHANGE_ON_NON_CASH",
            f"El vuelto de {change} supera el efectivo recibido ({cash_collected})",
            hint=(
                "Solo se da vuelto del efectivo. Cobra la tarjeta por el importe "
                "exacto o ajusta los cobros."
            ),
        )
    return change
