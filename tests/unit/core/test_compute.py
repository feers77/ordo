"""Tests de campos calculados, grafo de dependencias y caché (F2-03)."""

from decimal import Decimal

import pytest
from ordo_core.cache import RecordCache
from ordo_core.compute import depends
from ordo_core.errors import KernelError
from ordo_core.fields import Char, Integer, Many2one, Monetary, One2many
from ordo_core.model import Model
from ordo_core.registry import Module, Registry


def sales_registry() -> Registry:
    class Country(Model):
        _name = "res.country"
        _description = "País"

        code = Char(agent_hint="Código ISO", examples=["CL"])

    class Partner(Model):
        _name = "res.partner"
        _description = "Contacto"

        name = Char(agent_hint="Nombre", examples=["ACME"])
        country_id = Many2one("res.country", agent_hint="País", examples=["1"])

    class OrderLine(Model):
        _name = "sale.order.line"
        _description = "Línea de orden"

        order_id = Many2one("sale.order", agent_hint="Orden", examples=["1"])
        price_total = Monetary(agent_hint="Total de línea", examples=["1000.00"])
        quantity = Integer(agent_hint="Cantidad", examples=["10"])

    class SaleOrder(Model):
        _name = "sale.order"
        _description = "Orden de venta"

        name = Char(agent_hint="Número", examples=["SO0001"])
        partner_id = Many2one("res.partner", agent_hint="Cliente", examples=["1"])
        line_ids = One2many("sale.order.line", "order_id", agent_hint="Líneas", examples=["[1,2]"])
        amount_untaxed = Monetary(
            compute="_compute_amounts",
            store=True,
            agent_hint="Base imponible",
            examples=["9500.00"],
        )
        amount_total = Monetary(
            compute="_compute_total",
            store=True,
            agent_hint="Total",
            examples=["11305.00"],
        )
        partner_country = Char(
            related="partner_id.country_id.code",
            store=False,
            agent_hint="País del cliente",
            examples=["CL"],
        )

        @depends("line_ids.price_total")
        def _compute_amounts(self, records: list[dict]) -> None:  # type: ignore[type-arg]
            for record in records:
                record["amount_untaxed"] = sum(
                    (line["price_total"] for line in record.get("line_ids", [])),
                    Decimal("0"),
                )

        @depends("amount_untaxed")
        def _compute_total(self, records: list[dict]) -> None:  # type: ignore[type-arg]
            for record in records:
                record["amount_total"] = record["amount_untaxed"] * Decimal("1.19")

    return Registry.build([Module("sales", models=[Country, Partner, OrderLine, SaleOrder])])


class TestDependencyGraph:
    def test_direct_dependency_registered(self) -> None:
        graph = sales_registry().dependency_graph
        affected = graph.affected("sale.order", ["amount_untaxed"])
        assert ("sale.order", "amount_total") in affected

    def test_relational_dependency_registered(self) -> None:
        graph = sales_registry().dependency_graph
        affected = graph.affected("sale.order.line", ["price_total"])
        assert ("sale.order", "amount_untaxed") in affected

    def test_cascade_reaches_transitive_dependents(self) -> None:
        graph = sales_registry().dependency_graph
        affected = graph.affected("sale.order.line", ["price_total"])
        assert ("sale.order", "amount_total") in affected

    def test_topological_order_computes_upstream_first(self) -> None:
        graph = sales_registry().dependency_graph
        affected = graph.affected("sale.order.line", ["price_total"])
        assert affected.index(("sale.order", "amount_untaxed")) < affected.index(
            ("sale.order", "amount_total")
        )

    def test_unrelated_change_affects_nothing(self) -> None:
        graph = sales_registry().dependency_graph
        assert graph.affected("sale.order", ["name"]) == []

    def test_cycle_is_detected_at_build(self) -> None:
        class Circular(Model):
            _name = "circular.model"
            _description = "Circular"

            a = Integer(compute="_compute_a", agent_hint="A", examples=["1"])
            b = Integer(compute="_compute_b", agent_hint="B", examples=["1"])

            @depends("b")
            def _compute_a(self, records: list[dict]) -> None:  # type: ignore[type-arg]
                pass

            @depends("a")
            def _compute_b(self, records: list[dict]) -> None:  # type: ignore[type-arg]
                pass

        with pytest.raises(KernelError) as exc:
            Registry.build([Module("circ", models=[Circular])])
        assert exc.value.code == "COMPUTE_DEPENDENCY_CYCLE"


class TestComputeValidation:
    def test_missing_compute_method_rejected(self) -> None:
        class Bad(Model):
            _name = "bad.compute"
            _description = "Malo"

            total = Integer(compute="_no_existe", agent_hint="Total", examples=["1"])

        with pytest.raises(KernelError) as exc:
            Registry.build([Module("bad", models=[Bad])])
        assert exc.value.code == "COMPUTE_METHOD_MISSING"

    def test_depends_on_unknown_field_rejected(self) -> None:
        class Bad(Model):
            _name = "bad.depends"
            _description = "Malo"

            total = Integer(compute="_compute_total", agent_hint="Total", examples=["1"])

            @depends("campo_fantasma")
            def _compute_total(self, records: list[dict]) -> None:  # type: ignore[type-arg]
                pass

        with pytest.raises(KernelError) as exc:
            Registry.build([Module("bad", models=[Bad])])
        assert exc.value.code == "COMPUTE_UNKNOWN_DEPENDENCY"

    def test_compute_without_depends_rejected(self) -> None:
        class Bad(Model):
            _name = "bad.nodepends"
            _description = "Malo"

            total = Integer(compute="_compute_total", agent_hint="Total", examples=["1"])

            def _compute_total(self, records: list[dict]) -> None:  # type: ignore[type-arg]
                pass

        with pytest.raises(KernelError) as exc:
            Registry.build([Module("bad", models=[Bad])])
        assert exc.value.code == "COMPUTE_MISSING_DEPENDS"


class TestRelatedFields:
    def test_related_resolves_to_compute(self) -> None:
        registry = sales_registry()
        field = registry["sale.order"].fields["partner_country"]
        assert field.related == "partner_id.country_id.code"
        assert field.compute is not None

    def test_related_registers_dependency(self) -> None:
        graph = sales_registry().dependency_graph
        affected = graph.affected("res.partner", ["country_id"])
        assert ("sale.order", "partner_country") in affected

    def test_invalid_related_path_rejected(self) -> None:
        class Bad(Model):
            _name = "bad.related"
            _description = "Malo"

            partner_id = Many2one("res.partner", agent_hint="Cliente", examples=["1"])
            ghost = Char(related="partner_id.no_existe", agent_hint="Fantasma", examples=["x"])

        class Partner(Model):
            _name = "res.partner"
            _description = "Contacto"

            name = Char(agent_hint="Nombre", examples=["ACME"])

        with pytest.raises(KernelError) as exc:
            Registry.build([Module("bad", models=[Partner, Bad])])
        assert exc.value.code == "COMPUTE_INVALID_RELATED"


class TestRecordCache:
    def test_get_and_set(self) -> None:
        cache = RecordCache()
        cache.set("sale.order", 1, "amount_total", Decimal("100"))
        assert cache.get("sale.order", 1, "amount_total") == Decimal("100")

    def test_miss_returns_sentinel(self) -> None:
        cache = RecordCache()
        assert cache.get("sale.order", 1, "amount_total") is RecordCache.MISS

    def test_invalidate_field_cascades_to_dependents(self) -> None:
        cache = RecordCache(graph=sales_registry().dependency_graph)
        cache.set("sale.order", 1, "amount_untaxed", Decimal("100"))
        cache.set("sale.order", 1, "amount_total", Decimal("119"))
        cache.invalidate("sale.order", [1], ["amount_untaxed"])
        assert cache.get("sale.order", 1, "amount_untaxed") is RecordCache.MISS
        assert cache.get("sale.order", 1, "amount_total") is RecordCache.MISS

    def test_invalidate_does_not_touch_other_records(self) -> None:
        cache = RecordCache(graph=sales_registry().dependency_graph)
        cache.set("sale.order", 1, "amount_total", Decimal("119"))
        cache.set("sale.order", 2, "amount_total", Decimal("238"))
        cache.invalidate("sale.order", [1], ["amount_untaxed"])
        assert cache.get("sale.order", 2, "amount_total") == Decimal("238")

    def test_invalidate_all_clears(self) -> None:
        cache = RecordCache()
        cache.set("sale.order", 1, "name", "SO1")
        cache.invalidate_all()
        assert cache.get("sale.order", 1, "name") is RecordCache.MISS


class TestBatchRecomputation:
    def test_compute_receives_all_records_at_once(self) -> None:
        registry = sales_registry()
        model = registry["sale.order"]
        calls: list[int] = []

        def spy(records: list[dict]) -> None:  # type: ignore[type-arg]
            calls.append(len(records))
            for record in records:
                record["amount_untaxed"] = Decimal("10")

        records = [{"id": i} for i in range(5)]
        model.run_compute("_compute_amounts", records, implementation=spy)
        assert calls == [5]  # una sola llamada para los 5 registros
        assert all(r["amount_untaxed"] == Decimal("10") for r in records)


class TestDomainsRejectNonStoredComputes:
    def test_filter_by_non_stored_compute_rejected(self) -> None:
        from ordo_core.domains import DomainCompiler

        compiler = DomainCompiler(sales_registry(), schema="t_acme")
        with pytest.raises(KernelError) as exc:
            compiler.select(model="sale.order", domain=[("partner_country", "=", "CL")])
        assert exc.value.code == "DOMAIN_FIELD_NOT_STORED"

    def test_filter_by_stored_compute_allowed(self) -> None:
        from ordo_core.domains import DomainCompiler

        compiler = DomainCompiler(sales_registry(), schema="t_acme")
        stmt = compiler.select(model="sale.order", domain=[("amount_untaxed", ">", 1000)])
        assert "amount_untaxed" in str(stmt.compile())
