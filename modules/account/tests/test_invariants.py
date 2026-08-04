"""Invariantes contables con property-based testing.

Estos no son tests que acompañan al código: son la especificación. Un
asiento que no cuadra, una partida negativa o un asiento contabilizado que
se modifica son errores que no se detectan hasta el cierre, cuando ya son
caros de arreglar.
"""

from decimal import Decimal

import pytest
from hypothesis import assume, given, settings
from hypothesis import strategies as st

from modules.account.services import AccountingError, validate_lines

amounts = st.decimals(
    min_value=Decimal("0.01"),
    max_value=Decimal("1000000"),
    places=2,
    allow_nan=False,
    allow_infinity=False,
)


def line(debit: Decimal = Decimal(0), credit: Decimal = Decimal(0)) -> dict:  # type: ignore[type-arg]
    return {"account_id": 1, "debit": debit, "credit": credit, "name": "x"}


class TestDoubleEntry:
    @settings(max_examples=200, deadline=None)
    @given(amount=amounts)
    def test_balanced_move_is_accepted(self, amount: Decimal) -> None:
        """Debe igual a haber: el asiento cuadra sea cual sea el importe."""
        total = validate_lines([line(debit=amount), line(credit=amount)])
        assert total == amount

    @settings(max_examples=200, deadline=None)
    @given(debit=amounts, credit=amounts)
    def test_unbalanced_move_is_always_rejected(self, debit: Decimal, credit: Decimal) -> None:
        assume(debit != credit)
        with pytest.raises(AccountingError) as exc:
            validate_lines([line(debit=debit), line(credit=credit)])
        assert exc.value.code == "ACCOUNT_UNBALANCED"

    @settings(max_examples=100, deadline=None)
    @given(parts=st.lists(amounts, min_size=2, max_size=8))
    def test_many_lines_still_balance(self, parts: list[Decimal]) -> None:
        """Un asiento con N partidas al debe y su contrapartida única cuadra."""
        total = sum(parts, Decimal(0))
        lines = [line(debit=p) for p in parts] + [line(credit=total)]
        assert validate_lines(lines) == total

    @settings(max_examples=100, deadline=None)
    @given(amount=amounts)
    def test_order_of_lines_does_not_matter(self, amount: Decimal) -> None:
        """Reordenar las partidas no cambia la validez del asiento."""
        forward = validate_lines([line(debit=amount), line(credit=amount)])
        backward = validate_lines([line(credit=amount), line(debit=amount)])
        assert forward == backward


class TestLineRules:
    @settings(max_examples=100, deadline=None)
    @given(debit=amounts, credit=amounts)
    def test_line_cannot_have_both_sides(self, debit: Decimal, credit: Decimal) -> None:
        with pytest.raises(AccountingError) as exc:
            validate_lines([line(debit=debit, credit=credit)])
        assert exc.value.code in {"ACCOUNT_LINE_BOTH_SIDES", "ACCOUNT_UNBALANCED"}

    @settings(max_examples=100, deadline=None)
    @given(amount=amounts)
    def test_negative_amounts_are_rejected(self, amount: Decimal) -> None:
        """Un haber negativo es un debe mal escrito; se rechaza en vez de aceptarlo."""
        with pytest.raises(AccountingError) as exc:
            validate_lines([line(debit=-amount), line(credit=-amount)])
        assert exc.value.code == "ACCOUNT_NEGATIVE_AMOUNT"

    def test_empty_line_rejected(self) -> None:
        with pytest.raises(AccountingError) as exc:
            validate_lines([line(), line()])
        assert exc.value.code == "ACCOUNT_LINE_EMPTY"

    def test_line_without_account_rejected(self) -> None:
        with pytest.raises(AccountingError) as exc:
            validate_lines(
                [
                    {"debit": Decimal("10"), "credit": Decimal(0)},
                    {"account_id": 1, "debit": Decimal(0), "credit": Decimal("10")},
                ]
            )
        assert exc.value.code == "ACCOUNT_LINE_NO_ACCOUNT"

    def test_empty_move_rejected(self) -> None:
        with pytest.raises(AccountingError) as exc:
            validate_lines([])
        assert exc.value.code == "ACCOUNT_MOVE_EMPTY"


class TestMoneyIsNeverFloat:
    def test_float_amount_rejected(self) -> None:
        """Un float en un importe contable es un descuadre esperando ocurrir."""
        with pytest.raises(AccountingError) as exc:
            validate_lines(
                [
                    {"account_id": 1, "debit": 100.5, "credit": 0},
                    {"account_id": 2, "debit": 0, "credit": 100.5},
                ]
            )
        assert exc.value.code == "ACCOUNT_FLOAT_AMOUNT"

    def test_decimal_string_is_accepted(self) -> None:
        total = validate_lines(
            [
                {"account_id": 1, "debit": "100.50", "credit": "0"},
                {"account_id": 2, "debit": "0", "credit": "100.50"},
            ]
        )
        assert total == Decimal("100.50")

    @settings(max_examples=100, deadline=None)
    @given(cents=st.integers(min_value=1, max_value=99))
    def test_no_precision_loss_on_cents(self, cents: int) -> None:
        """Los céntimos no se pierden: es donde el float rompe la contabilidad."""
        amount = Decimal(f"0.{cents:02d}")
        total = validate_lines([line(debit=amount), line(credit=amount)])
        assert total == amount
        assert str(total) == str(amount)
