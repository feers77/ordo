"""Cantidad a reponer: invariantes.

Pedir de menos deja la tienda sin talla M el sábado; pedir de más deja la plata
parada en bodega. Las dos formas de equivocarse son silenciosas, así que se
prueban como propiedades y no como ejemplos.
"""

from decimal import Decimal

import pytest
from hypothesis import assume, given, settings
from hypothesis import strategies as st

from modules.stock.replenishment import (
    ZERO,
    ReplenishError,
    suggested_quantity,
    validate_range,
)

quantities = st.decimals(
    min_value=Decimal("0"), max_value=Decimal("10000"), places=2, allow_nan=False
)
multiples = st.decimals(min_value=Decimal("1"), max_value=Decimal("100"), places=0, allow_nan=False)


class TestSuggestedQuantity:
    @given(quantities, quantities, quantities)
    @settings(max_examples=400, deadline=None)
    def test_never_negative(self, on_hand: Decimal, minimum: Decimal, maximum: Decimal) -> None:
        assume(minimum <= maximum)
        assert suggested_quantity(on_hand, minimum, maximum) >= ZERO

    @given(quantities, quantities, quantities)
    @settings(max_examples=400, deadline=None)
    def test_replenishing_reaches_the_target(
        self, on_hand: Decimal, minimum: Decimal, maximum: Decimal
    ) -> None:
        """Después de reponer, el stock nunca queda bajo el mínimo. Si quedara,
        la alerta volvería a dispararse sola y el traslado no habría servido."""
        assume(minimum <= maximum)
        result = suggested_quantity(on_hand, minimum, maximum)
        if result > ZERO:
            assert on_hand + result >= minimum

    @given(quantities, quantities, quantities)
    @settings(max_examples=400, deadline=None)
    def test_above_the_minimum_nothing_is_ordered(
        self, on_hand: Decimal, minimum: Decimal, maximum: Decimal
    ) -> None:
        """La regla se dispara bajo el mínimo, no bajo el máximo. Reponer en
        cada venta llenaría la bodega de traslados de una unidad."""
        assume(minimum <= maximum)
        assume(on_hand >= minimum)
        assert suggested_quantity(on_hand, minimum, maximum) == ZERO

    @given(quantities, quantities, quantities, multiples)
    @settings(max_examples=400, deadline=None)
    def test_the_result_is_a_multiple_when_one_is_given(
        self, on_hand: Decimal, minimum: Decimal, maximum: Decimal, multiple: Decimal
    ) -> None:
        assume(minimum <= maximum)
        result = suggested_quantity(on_hand, minimum, maximum, multiple=multiple)
        assert result % multiple == ZERO

    @given(quantities, quantities, quantities, multiples)
    @settings(max_examples=400, deadline=None)
    def test_rounding_up_never_falls_short(
        self, on_hand: Decimal, minimum: Decimal, maximum: Decimal, multiple: Decimal
    ) -> None:
        """Quedarse corto es el error caro: si faltan 13 y la caja es de 12, se
        piden 24."""
        assume(minimum <= maximum)
        exact = suggested_quantity(on_hand, minimum, maximum)
        rounded = suggested_quantity(on_hand, minimum, maximum, multiple=multiple)
        assert rounded >= exact

    def test_the_classic_case(self) -> None:
        assert suggested_quantity(Decimal("3"), Decimal("5"), Decimal("20")) == Decimal("17")

    def test_a_box_of_twelve(self) -> None:
        assert suggested_quantity(
            Decimal("0"), Decimal("5"), Decimal("13"), multiple=Decimal("12")
        ) == Decimal("24")

    def test_a_multiple_of_zero_is_ignored_not_a_division_by_zero(self) -> None:
        assert suggested_quantity(
            Decimal("0"), Decimal("5"), Decimal("20"), multiple=ZERO
        ) == Decimal("20")


class TestValidateRange:
    def test_a_minimum_above_the_maximum_is_refused(self) -> None:
        with pytest.raises(ReplenishError) as excinfo:
            validate_range(Decimal("50"), Decimal("10"))
        assert excinfo.value.code == "STOCK_RULE_INVALID_RANGE"

    def test_negative_levels_are_refused(self) -> None:
        with pytest.raises(ReplenishError) as excinfo:
            validate_range(Decimal("-1"), Decimal("10"))
        assert excinfo.value.code == "STOCK_RULE_INVALID_RANGE"

    def test_equal_levels_are_allowed(self) -> None:
        validate_range(Decimal("10"), Decimal("10"))
