"""Arqueo y cobros: invariantes en Decimal.

El arqueo es donde una tienda detecta el robo hormiga. Un redondeo mal puesto o
una suma que depende del orden lo esconde, y eso no se ve en un test de ejemplo.
"""

from decimal import Decimal

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from modules.pos.cash import (
    ZERO,
    CashError,
    difference,
    expected_cash,
    money,
    validate_payments,
)

amounts = st.decimals(
    min_value=Decimal("0"), max_value=Decimal("1000000"), places=2, allow_nan=False
)
positive = st.decimals(
    min_value=Decimal("0.01"), max_value=Decimal("100000"), places=2, allow_nan=False
)


class TestExpectedCash:
    @given(positive, st.lists(amounts, max_size=8))
    @settings(max_examples=300, deadline=None)
    def test_order_of_payments_does_not_change_the_total(
        self, opening: Decimal, payments: list[Decimal]
    ) -> None:
        """Si el esperado dependiera del orden, dos arqueos del mismo turno
        darían distinto y la diferencia sería ruido."""
        forward = expected_cash(opening=opening, cash_received=payments, change_given=[])
        backward = expected_cash(
            opening=opening, cash_received=list(reversed(payments)), change_given=[]
        )
        assert forward == backward

    @given(positive, st.lists(amounts, max_size=6), st.lists(amounts, max_size=6))
    @settings(max_examples=300, deadline=None)
    def test_change_given_lowers_it_exactly(
        self, opening: Decimal, received: list[Decimal], change: list[Decimal]
    ) -> None:
        with_change = expected_cash(opening=opening, cash_received=received, change_given=change)
        without = expected_cash(opening=opening, cash_received=received, change_given=[])
        assert without - with_change == money(sum(change, ZERO))

    @given(positive, amounts)
    @settings(max_examples=200, deadline=None)
    def test_withdrawals_lower_it_exactly(self, opening: Decimal, taken: Decimal) -> None:
        assert expected_cash(
            opening=opening, cash_received=[], change_given=[], withdrawals=taken
        ) == money(opening - taken)

    def test_a_shift_without_sales_is_exactly_its_float(self) -> None:
        assert expected_cash(
            opening=Decimal("50000"), cash_received=[], change_given=[]
        ) == Decimal("50000.00")


class TestDifference:
    @given(amounts, amounts)
    @settings(max_examples=300, deadline=None)
    def test_swapping_the_sides_flips_the_sign(self, counted: Decimal, expected: Decimal) -> None:
        assert difference(counted, expected) == -difference(expected, counted)

    @given(amounts)
    @settings(max_examples=200, deadline=None)
    def test_counting_exactly_leaves_no_difference(self, value: Decimal) -> None:
        assert difference(value, value) == ZERO

    def test_a_shortfall_is_negative(self) -> None:
        assert difference(Decimal("213500"), Decimal("214000")) == Decimal("-500.00")


class TestValidatePayments:
    @given(positive, positive)
    @settings(max_examples=300, deadline=None)
    def test_change_is_what_was_collected_minus_the_ticket(
        self, total: Decimal, extra: Decimal
    ) -> None:
        collected = total + extra
        change = validate_payments(total, [{"amount": collected, "method_type": "cash"}])
        assert change == money(extra)
        assert change >= ZERO

    @given(st.lists(positive, min_size=1, max_size=5))
    @settings(max_examples=300, deadline=None)
    def test_paying_the_exact_amount_gives_no_change(self, parts: list[Decimal]) -> None:
        total = sum(parts, ZERO)
        payments = [{"amount": part, "method_type": "card"} for part in parts]
        assert validate_payments(total, payments) == ZERO

    def test_mixed_payment_covers_the_ticket(self) -> None:
        change = validate_payments(
            Decimal("23800"),
            [
                {"amount": Decimal("10000"), "method_type": "cash"},
                {"amount": Decimal("13800"), "method_type": "card"},
            ],
        )
        assert change == ZERO

    def test_not_covering_the_ticket_is_rejected(self) -> None:
        with pytest.raises(CashError) as excinfo:
            validate_payments(
                Decimal("23800"), [{"amount": Decimal("10000"), "method_type": "cash"}]
            )
        assert excinfo.value.code == "POS_PAYMENT_INSUFFICIENT"

    def test_a_ticket_without_payments_is_rejected(self) -> None:
        with pytest.raises(CashError) as excinfo:
            validate_payments(Decimal("1000"), [])
        assert excinfo.value.code == "POS_PAYMENT_INSUFFICIENT"

    def test_a_non_positive_payment_is_not_a_payment(self) -> None:
        with pytest.raises(CashError) as excinfo:
            validate_payments(Decimal("1000"), [{"amount": ZERO, "method_type": "cash"}])
        assert excinfo.value.code == "POS_PAYMENT_INSUFFICIENT"

    def test_change_cannot_come_out_of_a_card(self) -> None:
        """Dar vuelto de una tarjeta es sacar del cajón plata que no entró: la
        caja se desangra y el arqueo lo ve como faltante sin causa."""
        with pytest.raises(CashError) as excinfo:
            validate_payments(
                Decimal("10000"), [{"amount": Decimal("15000"), "method_type": "card"}]
            )
        assert excinfo.value.code == "POS_CHANGE_ON_NON_CASH"

    def test_change_may_exceed_a_single_cash_payment_if_cash_covers_it(self) -> None:
        change = validate_payments(
            Decimal("10000"),
            [
                {"amount": Decimal("5000"), "method_type": "card"},
                {"amount": Decimal("8000"), "method_type": "cash"},
            ],
        )
        assert change == Decimal("3000.00")
