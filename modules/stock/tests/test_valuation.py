"""El costo promedio, probado como propiedad: nunca inventa valor."""

from decimal import Decimal

import pytest
from hypothesis import assume, given, settings
from hypothesis import strategies as st

from modules.stock.valuation import ValuationError, money, new_average

quantities = st.decimals(
    min_value=Decimal("0.01"),
    max_value=Decimal("10000"),
    places=2,
    allow_nan=False,
    allow_infinity=False,
)
prices = st.decimals(
    min_value=Decimal("0.01"),
    max_value=Decimal("9999999"),
    places=2,
    allow_nan=False,
    allow_infinity=False,
)


class TestAverageCost:
    @settings(max_examples=300, deadline=None)
    @given(receipts=st.lists(st.tuples(quantities, prices), min_size=1, max_size=8))
    def test_average_stays_within_received_prices(
        self, receipts: list[tuple[Decimal, Decimal]]
    ) -> None:
        """Tras cualquier serie de entradas, el promedio vive entre min y max."""
        on_hand = Decimal("0")
        avg = Decimal("0")
        for quantity, price in receipts:
            avg = new_average(on_hand, avg, quantity, price)
            on_hand += quantity
        prices_seen = [price for _, price in receipts]
        assert min(prices_seen) - Decimal("0.01") <= avg <= max(prices_seen) + Decimal("0.01")

    @settings(max_examples=200, deadline=None)
    @given(quantity=quantities, price=prices)
    def test_first_receipt_sets_the_average_to_its_price(
        self, quantity: Decimal, price: Decimal
    ) -> None:
        assert new_average(Decimal("0"), Decimal("0"), quantity, price) == money(price)

    @settings(max_examples=200, deadline=None)
    @given(on_hand=quantities, avg=prices, quantity=quantities, price=prices)
    def test_total_value_is_conserved(
        self, on_hand: Decimal, avg: Decimal, quantity: Decimal, price: Decimal
    ) -> None:
        """Valor previo + valor de la entrada ≈ existencias nuevas x promedio nuevo."""
        assume(on_hand > 0)
        updated = new_average(on_hand, avg, quantity, price)
        expected = on_hand * avg + quantity * price
        actual = (on_hand + quantity) * updated
        # la única pérdida permitida es el redondeo del promedio a 2 decimales
        assert abs(actual - expected) <= (on_hand + quantity) * Decimal("0.005") + Decimal("0.01")

    def test_negative_stock_resets_to_incoming_price(self) -> None:
        """Con stock fantasma no se promedia: la entrada define el costo."""
        assert new_average(Decimal("-5"), Decimal("100"), Decimal("10"), Decimal("40")) == Decimal(
            "40.00"
        )

    def test_zero_or_negative_entry_is_rejected(self) -> None:
        with pytest.raises(ValuationError) as excinfo:
            new_average(Decimal("1"), Decimal("1"), Decimal("0"), Decimal("1"))
        assert excinfo.value.code == "STOCK_INVALID_QUANTITY"

    def test_negative_cost_is_rejected(self) -> None:
        with pytest.raises(ValuationError) as excinfo:
            new_average(Decimal("1"), Decimal("1"), Decimal("1"), Decimal("-1"))
        assert excinfo.value.code == "STOCK_NEGATIVE_COST"
