"""Motor de impuestos (F4-02).

El cálculo de impuestos es donde un redondeo mal puesto produce diferencias
de un peso que un fiscalizador sí mira. Todo se hace en `Decimal` con
redondeo explícito, y el redondeo por línea o por documento es una decisión
declarada, no un efecto colateral.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

from ordo_core.errors import KernelError

ZERO = Decimal("0")
HUNDRED = Decimal("100")


class TaxError(KernelError):
    """Error del motor de impuestos con código estable."""


@dataclass(frozen=True)
class Tax:
    """Un impuesto aplicable a una línea.

    `price_include` distingue los dos mundos: en Chile el precio de lista
    suele ser neto y el IVA se agrega; en otros países el precio mostrado ya
    lo lleva dentro. Calcular mal esto cambia la base imponible.
    """

    code: str
    name: str
    amount: Decimal
    tax_type: str = "percent"  # percent | fixed
    price_include: bool = False
    is_withholding: bool = False
    sequence: int = 10
    base_affected_by: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if isinstance(self.amount, float):
            raise TaxError(
                "TAX_FLOAT_RATE",
                f"La tasa de '{self.code}' no puede ser float; usa Decimal",
            )
        if self.tax_type not in {"percent", "fixed"}:
            raise TaxError("TAX_INVALID_TYPE", f"Tipo de impuesto inválido: {self.tax_type}")


@dataclass
class TaxLine:
    code: str
    name: str
    base: Decimal
    amount: Decimal
    is_withholding: bool = False


@dataclass
class TaxResult:
    base: Decimal
    total_excluded: Decimal
    total_included: Decimal
    taxes: list[TaxLine] = field(default_factory=list)

    @property
    def total_withheld(self) -> Decimal:
        return sum((t.amount for t in self.taxes if t.is_withholding), ZERO)


def quantize(value: Decimal, decimals: int) -> Decimal:
    """Redondeo bancario explícito: medio hacia arriba, como exige el fisco."""
    exponent = Decimal(1).scaleb(-decimals)
    return value.quantize(exponent, rounding=ROUND_HALF_UP)


def _to_decimal(value: Any, where: str) -> Decimal:
    if isinstance(value, Decimal):
        return value
    if isinstance(value, float):
        raise TaxError("TAX_FLOAT_AMOUNT", f"{where} no admite float; usa Decimal o string decimal")
    return Decimal(str(value))


def compute_line(
    *,
    price_unit: Any,
    quantity: Any = 1,
    taxes: list[Tax],
    decimals: int = 2,
    discount_percent: Any = 0,
) -> TaxResult:
    """Calcula la base y los impuestos de una línea.

    El orden importa: primero se descuenta, luego se extrae el impuesto
    incluido si lo hay, y solo entonces se aplican los impuestos sobre la
    base resultante.
    """
    price = _to_decimal(price_unit, "price_unit")
    qty = _to_decimal(quantity, "quantity")
    discount = _to_decimal(discount_percent, "discount_percent")

    gross = price * qty
    if discount:
        gross = gross * (HUNDRED - discount) / HUNDRED

    ordered = sorted(taxes, key=lambda t: (t.sequence, t.code))
    included = [t for t in ordered if t.price_include and t.tax_type == "percent"]

    base = gross
    if included:
        # El precio ya lleva el impuesto: hay que sacarlo para llegar a la base.
        divisor = HUNDRED + sum((t.amount for t in included), ZERO)
        base = gross * HUNDRED / divisor

    base = quantize(base, decimals)

    lines: list[TaxLine] = []
    applied: dict[str, Decimal] = {}
    for tax in ordered:
        # `base_affected_by` nombra los impuestos cuyo importe se suma a la base
        # de este. Ser explícito evita la ambigüedad de "grava sobre lo anterior",
        # que depende del orden y produce resultados distintos según se listen.
        taxable = base + sum((applied.get(code, ZERO) for code in tax.base_affected_by), ZERO)
        raw = tax.amount * qty if tax.tax_type == "fixed" else taxable * tax.amount / HUNDRED
        amount = quantize(raw, decimals)
        applied[tax.code] = amount
        lines.append(
            TaxLine(
                code=tax.code,
                name=tax.name,
                base=quantize(taxable, decimals),
                amount=amount,
                is_withholding=tax.is_withholding,
            )
        )

    added = sum((t.amount for t in lines if not t.is_withholding), ZERO)
    return TaxResult(
        base=base,
        total_excluded=base,
        total_included=quantize(base + added, decimals),
        taxes=lines,
    )


def compute_document(
    lines: list[dict[str, Any]],
    *,
    decimals: int = 2,
    round_per_line: bool = True,
) -> TaxResult:
    """Agrega varias líneas en el total del documento.

    `round_per_line=True` redondea cada línea y suma; `False` suma en alta
    precisión y redondea al final. Los dos criterios existen en la práctica y
    dan resultados distintos por céntimos: se elige, no se improvisa.
    """
    per_tax: dict[str, TaxLine] = {}
    base_total = ZERO

    for index, line in enumerate(lines):
        taxes = line.get("taxes", [])
        result = compute_line(
            price_unit=line.get("price_unit", 0),
            quantity=line.get("quantity", 1),
            taxes=taxes,
            decimals=decimals if round_per_line else decimals + 6,
            discount_percent=line.get("discount_percent", 0),
        )
        if not isinstance(taxes, list):
            raise TaxError("TAX_INVALID_LINE", f"Línea {index}: 'taxes' debe ser una lista")
        base_total += result.base
        for tax_line in result.taxes:
            existing = per_tax.get(tax_line.code)
            if existing is None:
                per_tax[tax_line.code] = TaxLine(
                    code=tax_line.code,
                    name=tax_line.name,
                    base=tax_line.base,
                    amount=tax_line.amount,
                    is_withholding=tax_line.is_withholding,
                )
            else:
                existing.base += tax_line.base
                existing.amount += tax_line.amount

    aggregated = list(per_tax.values())
    for tax_line in aggregated:
        tax_line.base = quantize(tax_line.base, decimals)
        tax_line.amount = quantize(tax_line.amount, decimals)

    base_total = quantize(base_total, decimals)
    added = sum((t.amount for t in aggregated if not t.is_withholding), ZERO)
    return TaxResult(
        base=base_total,
        total_excluded=base_total,
        total_included=quantize(base_total + added, decimals),
        taxes=sorted(aggregated, key=lambda t: t.code),
    )
