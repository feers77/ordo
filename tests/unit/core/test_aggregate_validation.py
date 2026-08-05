"""Validaciones del compilador de agregaciones (F2-08), sin base de datos."""

import pytest
from ordo_core.domains import DomainCompiler
from ordo_core.errors import KernelError
from ordo_core.fields import Char, Date, Many2one, Monetary
from ordo_core.model import Model
from ordo_core.registry import Module, Registry


def registry() -> Registry:
    class Partner(Model):
        _name = "res.partner"
        _description = "Contacto"

        name = Char(agent_hint="Nombre", examples=["ACME"])

    class SaleOrder(Model):
        _name = "sale.order"
        _description = "Orden de venta"

        name = Char(agent_hint="Número", examples=["SO0001"])
        state = Char(agent_hint="Estado", examples=["draft"])
        date_order = Date(agent_hint="Fecha", examples=["2026-08-04"])
        amount_total = Monetary(agent_hint="Total", examples=["1000.00"])
        partner_id = Many2one("res.partner", agent_hint="Cliente", examples=["1"])

    return Registry.build([Module("demo", models=[Partner, SaleOrder])])


def compiler() -> DomainCompiler:
    return DomainCompiler(registry(), schema="t_acme")


class TestAggregateSpecs:
    def test_unknown_aggregate_is_rejected(self) -> None:
        with pytest.raises(KernelError) as exc:
            compiler().aggregate(model="sale.order", domain=[], aggregates=["median:amount_total"])
        assert exc.value.code == "AGGREGATE_UNKNOWN"
        assert "sum:<campo>" in (exc.value.hint or "")

    def test_sum_over_text_field_is_rejected(self) -> None:
        with pytest.raises(KernelError) as exc:
            compiler().aggregate(model="sale.order", domain=[], aggregates=["sum:name"])
        assert exc.value.code == "AGGREGATE_INVALID_FIELD"
        assert "name" in exc.value.message

    def test_sum_over_unknown_field_is_rejected(self) -> None:
        with pytest.raises(KernelError) as exc:
            compiler().aggregate(model="sale.order", domain=[], aggregates=["sum:no_existe"])
        assert exc.value.code == "AGGREGATE_INVALID_FIELD"

    def test_min_accepts_a_date_field(self) -> None:
        stmt = compiler().aggregate(
            model="sale.order", domain=[], aggregates=["min:date_order", "max:date_order"]
        )
        sql = str(stmt).lower()
        assert "min(" in sql
        assert "max(" in sql

    def test_avg_over_a_date_field_is_rejected(self) -> None:
        with pytest.raises(KernelError) as exc:
            compiler().aggregate(model="sale.order", domain=[], aggregates=["avg:date_order"])
        assert exc.value.code == "AGGREGATE_INVALID_FIELD"


class TestGroupBy:
    def test_unknown_group_field_is_rejected(self) -> None:
        with pytest.raises(KernelError) as exc:
            compiler().aggregate(model="sale.order", domain=[], group_by=["no_existe"])
        assert exc.value.code == "FIELD_UNKNOWN"

    def test_dotted_group_field_is_rejected(self) -> None:
        with pytest.raises(KernelError) as exc:
            compiler().aggregate(model="sale.order", domain=[], group_by=["partner_id.name"])
        assert exc.value.code == "FIELD_UNKNOWN"


class TestOrder:
    def test_invalid_order_is_rejected(self) -> None:
        with pytest.raises(KernelError) as exc:
            compiler().aggregate(
                model="sale.order",
                domain=[],
                group_by=["state"],
                aggregates=["count"],
                order="sum:amount_total desc",
            )
        assert exc.value.code == "AGGREGATE_INVALID_ORDER"

    def test_injection_in_order_is_rejected(self) -> None:
        with pytest.raises(KernelError) as exc:
            compiler().aggregate(
                model="sale.order", domain=[], group_by=["state"], order="state; DROP TABLE x"
            )
        assert exc.value.code == "AGGREGATE_INVALID_ORDER"

    def test_order_by_a_requested_aggregate(self) -> None:
        stmt = compiler().aggregate(
            model="sale.order",
            domain=[],
            group_by=["partner_id"],
            aggregates=["count", "sum:amount_total"],
            order="sum:amount_total desc",
        )
        assert "ORDER BY" in str(stmt).upper()

    def test_order_by_a_grouped_field(self) -> None:
        stmt = compiler().aggregate(
            model="sale.order", domain=[], group_by=["state"], order="state"
        )
        assert "ORDER BY" in str(stmt).upper()


class TestCompiledStatement:
    def test_valid_aggregate_compiles_with_group_by(self) -> None:
        stmt = compiler().aggregate(
            model="sale.order",
            domain=[("state", "=", "confirmed")],
            group_by=["partner_id", "state"],
            aggregates=["count", "sum:amount_total", "avg:amount_total"],
            limit=10,
        )
        sql = str(stmt)
        assert "GROUP BY" in sql.upper()
        assert "sum:amount_total" in sql
        assert "LIMIT" in sql.upper()

    def test_without_group_by_there_is_no_group_clause(self) -> None:
        stmt = compiler().aggregate(model="sale.order", domain=[], aggregates=["count"])
        assert "GROUP BY" not in str(stmt).upper()

    def test_domain_values_travel_as_bound_parameters(self) -> None:
        payload = "x'; DROP TABLE sale_order; --"
        stmt = compiler().aggregate(
            model="sale.order",
            domain=[("state", "=", payload)],
            group_by=["state"],
            aggregates=["sum:amount_total"],
        )
        compiled = stmt.compile()
        assert "DROP TABLE" not in str(compiled)
        assert payload in compiled.params.values()

    def test_record_rules_are_applied(self) -> None:
        stmt = compiler().aggregate(
            model="sale.order",
            domain=[],
            group_by=["state"],
            rules={"global_and": [[("partner_id", "=", 7)]]},
        )
        assert "partner_id" in str(stmt)
