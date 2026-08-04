"""Tests del sistema de campos (F2-01) — escritos antes de implementar."""

import pytest
from ordo_core.errors import KernelError
from ordo_core.fields import (
    Boolean,
    Char,
    Integer,
    Many2one,
    Monetary,
    One2many,
    Selection,
)


class TestFieldBasics:
    def test_char_defaults(self) -> None:
        field = Char(agent_hint="Nombre", examples=["ACME"])
        assert field.required is False
        assert field.store is True
        assert field.agent_hint == "Nombre"

    def test_selection_validates_values(self) -> None:
        field = Selection(
            [("draft", "Borrador"), ("sale", "Confirmada")],
            agent_hint="Estado",
            examples=["draft"],
        )
        assert field.allowed_values == {"draft", "sale"}

    def test_selection_rejects_empty(self) -> None:
        with pytest.raises(KernelError) as exc:
            Selection([], agent_hint="x", examples=["y"])
        assert exc.value.code == "FIELD_INVALID_DEFINITION"

    def test_many2one_requires_comodel(self) -> None:
        with pytest.raises(KernelError):
            Many2one("", agent_hint="x", examples=["1"])

    def test_one2many_requires_inverse(self) -> None:
        with pytest.raises(KernelError):
            One2many("sale.order.line", "", agent_hint="Líneas", examples=[])

    def test_monetary_requires_currency_field(self) -> None:
        field = Monetary(agent_hint="Total", examples=["1000.00"])
        assert field.currency_field == "currency_id"

    def test_boolean_default_false(self) -> None:
        assert Boolean(agent_hint="Activo", examples=["true"]).default is False

    def test_integer_index_flag(self) -> None:
        assert Integer(index=True, agent_hint="Secuencia", examples=["10"]).index is True


class TestMoneyIsNeverFloat:
    def test_monetary_rejects_float_default(self) -> None:
        with pytest.raises(KernelError) as exc:
            Monetary(default=1.5, agent_hint="Total", examples=["1.50"])  # type: ignore[arg-type]
        assert exc.value.code == "FIELD_INVALID_DEFINITION"

    def test_monetary_accepts_decimal_default(self) -> None:
        from decimal import Decimal

        field = Monetary(default=Decimal("0"), agent_hint="Total", examples=["0.00"])
        assert field.default == Decimal("0")
