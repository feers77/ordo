"""Tests del motor de impuestos, incluidos los casos que producen descuadres.

Un impuesto mal redondeado no falla ruidosamente: produce una diferencia de
céntimos que aparece meses después en una fiscalización.
"""

from decimal import Decimal

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from modules.account.taxes import (
    Tax,
    TaxError,
    compute_document,
    compute_line,
    quantize,
)

IVA_CL = Tax(code="IVA19", name="IVA 19%", amount=Decimal("19"))
IVA_INCLUIDO = Tax(code="IVA19I", name="IVA 19% incluido", amount=Decimal("19"), price_include=True)
IVA_PY = Tax(code="IVA10", name="IVA 10%", amount=Decimal("10"))
IVA_PY_5 = Tax(code="IVA5", name="IVA 5%", amount=Decimal("5"))
RETENCION = Tax(code="RET10", name="Retención 10%", amount=Decimal("10"), is_withholding=True)


class TestPriceExcluded:
    def test_tax_added_over_net_price(self) -> None:
        result = compute_line(price_unit="100000", taxes=[IVA_CL], decimals=0)
        assert result.base == Decimal("100000")
        assert result.taxes[0].amount == Decimal("19000")
        assert result.total_included == Decimal("119000")

    def test_quantity_multiplies_base(self) -> None:
        result = compute_line(price_unit="1000", quantity=10, taxes=[IVA_CL], decimals=0)
        assert result.base == Decimal("10000")
        assert result.total_included == Decimal("11900")

    def test_discount_applies_before_tax(self) -> None:
        """El descuento reduce la base imponible, no el impuesto ya calculado."""
        result = compute_line(
            price_unit="1000", quantity=10, discount_percent=5, taxes=[IVA_CL], decimals=0
        )
        assert result.base == Decimal("9500")
        assert result.taxes[0].amount == Decimal("1805")


class TestPriceIncluded:
    def test_tax_extracted_from_gross_price(self) -> None:
        """Con precio con impuesto incluido, la base es menor que el precio."""
        result = compute_line(price_unit="119000", taxes=[IVA_INCLUIDO], decimals=0)
        assert result.base == Decimal("100000")
        assert result.taxes[0].amount == Decimal("19000")
        assert result.total_included == Decimal("119000")

    def test_included_and_excluded_agree_on_total(self) -> None:
        """Cobrar 119.000 con IVA incluido o 100.000 + IVA da lo mismo."""
        incluido = compute_line(price_unit="119000", taxes=[IVA_INCLUIDO], decimals=0)
        excluido = compute_line(price_unit="100000", taxes=[IVA_CL], decimals=0)
        assert incluido.total_included == excluido.total_included
        assert incluido.base == excluido.base


class TestWithholding:
    def test_withholding_does_not_increase_total(self) -> None:
        """Una retención no suma al total: se descuenta de lo que se paga."""
        result = compute_line(price_unit="100000", taxes=[IVA_CL, RETENCION], decimals=0)
        assert result.total_included == Decimal("119000")
        assert result.total_withheld == Decimal("10000")

    def test_withholding_is_reported_separately(self) -> None:
        result = compute_line(price_unit="100000", taxes=[RETENCION], decimals=0)
        assert result.taxes[0].is_withholding is True
        assert result.total_included == result.base


class TestCompoundTaxes:
    def test_tax_over_tax_uses_accumulated_base(self) -> None:
        """Un impuesto compuesto grava el importe que ya incluye el anterior."""
        segundo = Tax(
            code="ADIC",
            name="Adicional sobre IVA",
            amount=Decimal("10"),
            sequence=20,
            base_affected_by=("IVA19",),
        )
        result = compute_line(price_unit="100000", taxes=[IVA_CL, segundo], decimals=0)
        assert result.taxes[0].amount == Decimal("19000")
        # el segundo grava 100.000 + 19.000
        assert result.taxes[1].base == Decimal("119000")
        assert result.taxes[1].amount == Decimal("11900")

    def test_sequence_determines_order(self) -> None:
        primero = Tax(code="A", name="A", amount=Decimal("10"), sequence=1)
        segundo = Tax(code="B", name="B", amount=Decimal("5"), sequence=2)
        result = compute_line(price_unit="1000", taxes=[segundo, primero], decimals=2)
        assert [t.code for t in result.taxes] == ["A", "B"]


class TestRounding:
    def test_rounding_is_half_up(self) -> None:
        assert quantize(Decimal("0.125"), 2) == Decimal("0.13")
        assert quantize(Decimal("0.135"), 2) == Decimal("0.14")

    def test_currency_without_decimals(self) -> None:
        """El peso chileno no admite decimales: el impuesto se redondea a entero."""
        result = compute_line(price_unit="1990", taxes=[IVA_CL], decimals=0)
        assert result.taxes[0].amount == Decimal("378")  # 378.1 -> 378
        assert result.total_included == Decimal("2368")

    def test_per_line_and_per_document_can_differ(self) -> None:
        """Los dos criterios de redondeo existen y dan resultados distintos.

        No es un defecto: es una decisión que cada país o empresa toma, y el
        motor la hace explícita en vez de imponer una en silencio.
        """
        lines = [{"price_unit": "333.33", "quantity": 3, "taxes": [IVA_CL]}] * 3
        per_line = compute_document(lines, decimals=2, round_per_line=True)
        per_document = compute_document(lines, decimals=2, round_per_line=False)
        assert per_line.total_included >= per_document.total_included
        difference = per_line.total_included - per_document.total_included
        assert difference < Decimal("1")  # céntimos, no pesos


class TestDocumentAggregation:
    def test_same_tax_is_aggregated(self) -> None:
        lines = [
            {"price_unit": "100000", "taxes": [IVA_CL]},
            {"price_unit": "50000", "taxes": [IVA_CL]},
        ]
        result = compute_document(lines, decimals=0)
        assert len(result.taxes) == 1
        assert result.base == Decimal("150000")
        assert result.taxes[0].amount == Decimal("28500")

    def test_different_rates_reported_separately(self) -> None:
        """Paraguay tiene IVA 10% y 5%: el documento los informa por separado."""
        lines = [
            {"price_unit": "100000", "taxes": [IVA_PY]},
            {"price_unit": "100000", "taxes": [IVA_PY_5]},
        ]
        result = compute_document(lines, decimals=0)
        by_code = {t.code: t for t in result.taxes}
        assert by_code["IVA10"].amount == Decimal("10000")
        assert by_code["IVA5"].amount == Decimal("5000")
        assert result.total_included == Decimal("215000")

    def test_line_without_taxes_is_exempt(self) -> None:
        lines = [
            {"price_unit": "100000", "taxes": [IVA_CL]},
            {"price_unit": "50000", "taxes": []},
        ]
        result = compute_document(lines, decimals=0)
        assert result.base == Decimal("150000")
        assert result.total_included == Decimal("169000")


class TestMoneyRules:
    def test_float_price_rejected(self) -> None:
        with pytest.raises(TaxError) as exc:
            compute_line(price_unit=1000.5, taxes=[IVA_CL])
        assert exc.value.code == "TAX_FLOAT_AMOUNT"

    def test_float_rate_rejected(self) -> None:
        with pytest.raises(TaxError) as exc:
            Tax(code="X", name="X", amount=19.0)  # type: ignore[arg-type]
        assert exc.value.code == "TAX_FLOAT_RATE"


class TestProperties:
    @settings(max_examples=200, deadline=None)
    @given(amount=st.decimals(min_value=Decimal("1"), max_value=Decimal("10000000"), places=0))
    def test_included_and_excluded_are_inverse(self, amount: Decimal) -> None:
        """Extraer el impuesto de un precio con IVA y volver a agregarlo cierra."""
        excluido = compute_line(price_unit=amount, taxes=[IVA_CL], decimals=0)
        incluido = compute_line(
            price_unit=excluido.total_included, taxes=[IVA_INCLUIDO], decimals=0
        )
        # el redondeo puede mover un peso; más que eso sería un error de fórmula
        assert abs(incluido.base - excluido.base) <= Decimal("1")

    @settings(max_examples=200, deadline=None)
    @given(amount=st.decimals(min_value=Decimal("1"), max_value=Decimal("1000000"), places=2))
    def test_tax_never_exceeds_its_rate(self, amount: Decimal) -> None:
        result = compute_line(price_unit=amount, taxes=[IVA_CL], decimals=2)
        assert result.taxes[0].amount <= result.base * Decimal("0.19") + Decimal("0.01")

    @settings(max_examples=200, deadline=None)
    @given(amount=st.decimals(min_value=Decimal("1"), max_value=Decimal("1000000"), places=2))
    def test_total_equals_base_plus_taxes(self, amount: Decimal) -> None:
        """Invariante básico: el total es la base más los impuestos no retenidos."""
        result = compute_line(price_unit=amount, taxes=[IVA_CL, RETENCION], decimals=2)
        added = sum(t.amount for t in result.taxes if not t.is_withholding)
        assert result.total_included == quantize(result.base + added, 2)

    @settings(max_examples=100, deadline=None)
    @given(
        amounts=st.lists(
            st.decimals(min_value=Decimal("1"), max_value=Decimal("100000"), places=0),
            min_size=1,
            max_size=6,
        )
    )
    def test_document_total_matches_sum_of_lines(self, amounts: list[Decimal]) -> None:
        lines = [{"price_unit": a, "taxes": [IVA_CL]} for a in amounts]
        document = compute_document(lines, decimals=0)
        assert document.base == sum(amounts)
