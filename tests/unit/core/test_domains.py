"""Tests del compilador de dominios (F2-02) — escritos antes de implementar.

Incluye los tests de inyección, que son bloqueantes en CI.
"""

from typing import Any

import pytest
from ordo_core.domains import MAX_DEPTH, MAX_TERMS, DomainCompiler
from ordo_core.errors import KernelError
from ordo_core.fields import Boolean, Char, Integer, Many2one, Monetary
from ordo_core.model import Model
from ordo_core.registry import Module, Registry


def registry() -> Registry:
    class Country(Model):
        _name = "res.country"
        _description = "País"

        code = Char(agent_hint="Código ISO", examples=["CL"])

    class Partner(Model):
        _name = "res.partner"
        _description = "Contacto"

        name = Char(agent_hint="Nombre", examples=["ACME"])
        active = Boolean(agent_hint="Activo", examples=["true"])
        country_id = Many2one("res.country", agent_hint="País", examples=["1"])

    class SaleOrder(Model):
        _name = "sale.order"
        _description = "Orden de venta"

        name = Char(agent_hint="Número", examples=["SO0001"])
        state = Char(agent_hint="Estado", examples=["draft"])
        amount_total = Monetary(agent_hint="Total", examples=["1000.00"])
        sequence = Integer(agent_hint="Secuencia", examples=["10"])
        partner_id = Many2one("res.partner", agent_hint="Cliente", examples=["1"])

    return Registry.build([Module("demo", models=[Country, Partner, SaleOrder])])


def compiler() -> DomainCompiler:
    return DomainCompiler(registry(), schema="t_acme")


def sql_of(domain: list[Any], model: str = "sale.order") -> str:
    stmt = compiler().select(model=model, domain=domain)
    return str(stmt.compile(compile_kwargs={"literal_binds": False}))


class TestOperators:
    @pytest.mark.parametrize(
        ("operator", "fragment"),
        [
            ("=", "="),
            ("!=", "!="),
            (">", ">"),
            (">=", ">="),
            ("<", "<"),
            ("<=", "<="),
            ("like", "LIKE"),
            ("ilike", "lower"),
        ],
    )
    def test_operator_translates(self, operator: str, fragment: str) -> None:
        sql = sql_of([("name", operator, "SO0001")])
        assert fragment.lower() in sql.lower()

    def test_in_operator(self) -> None:
        sql = sql_of([("state", "in", ["draft", "sale"])])
        assert "IN" in sql.upper()

    def test_empty_in_is_constant_false(self) -> None:
        stmt = compiler().select(model="sale.order", domain=[("state", "in", [])])
        sql = str(stmt.compile(compile_kwargs={"literal_binds": True}))
        assert "false" in sql.lower()

    def test_none_becomes_is_null(self) -> None:
        sql = sql_of([("partner_id", "=", None)])
        assert "IS NULL" in sql.upper()

    def test_not_equal_none_becomes_is_not_null(self) -> None:
        sql = sql_of([("partner_id", "!=", None)])
        assert "IS NOT NULL" in sql.upper()

    def test_unknown_operator_rejected(self) -> None:
        with pytest.raises(KernelError) as exc:
            sql_of([("name", "=;DROP", "x")])
        assert exc.value.code == "DOMAIN_UNKNOWN_OPERATOR"


class TestLogic:
    def test_implicit_and(self) -> None:
        sql = sql_of([("state", "=", "sale"), ("sequence", ">", 1)])
        assert " AND " in sql.upper()

    def test_explicit_or(self) -> None:
        sql = sql_of(["|", ("state", "=", "sale"), ("state", "=", "draft")])
        assert " OR " in sql.upper()

    def test_not(self) -> None:
        # SQLAlchemy simplifica NOT (a = b) a (a != b): equivalente en SQL
        sql = sql_of(["!", ("state", "=", "sale")]).upper()
        assert "NOT" in sql or "!=" in sql

    def test_not_over_compound_keeps_negation(self) -> None:
        sql = sql_of(["!", "|", ("state", "=", "sale"), ("sequence", ">", 1)]).upper()
        assert "NOT" in sql

    def test_nested_logic(self) -> None:
        sql = sql_of(
            [
                ("state", "=", "sale"),
                "|",
                ("sequence", ">", 10),
                "&",
                ("name", "like", "SO%"),
                ("sequence", "<", 5),
            ]
        )
        assert " OR " in sql.upper()
        assert " AND " in sql.upper()

    def test_incomplete_operator_rejected(self) -> None:
        with pytest.raises(KernelError) as exc:
            sql_of(["|", ("state", "=", "sale")])
        assert exc.value.code == "DOMAIN_MALFORMED"

    def test_trailing_terms_rejected(self) -> None:
        with pytest.raises(KernelError):
            sql_of([("state", "=", "sale"), "&"])


class TestDottedPaths:
    def test_single_hop_join(self) -> None:
        sql = sql_of([("partner_id.name", "=", "ACME")])
        assert "JOIN" in sql.upper()
        assert "res_partner" in sql

    def test_two_hop_join(self) -> None:
        sql = sql_of([("partner_id.country_id.code", "=", "CL")])
        assert sql.upper().count("JOIN") >= 2
        assert "res_country" in sql

    def test_path_through_non_relational_field_rejected(self) -> None:
        with pytest.raises(KernelError) as exc:
            sql_of([("name.foo", "=", "x")])
        assert exc.value.code == "DOMAIN_INVALID_PATH"

    def test_depth_limit_enforced(self) -> None:
        path = ".".join(["partner_id"] * (MAX_DEPTH + 2)) + ".name"
        with pytest.raises(KernelError) as exc:
            sql_of([(path, "=", "x")])
        assert exc.value.code == "DOMAIN_PATH_TOO_DEEP"


class TestSchemaValidation:
    def test_unknown_field_rejected(self) -> None:
        with pytest.raises(KernelError) as exc:
            sql_of([("no_existe", "=", 1)])
        assert exc.value.code == "DOMAIN_UNKNOWN_FIELD"

    def test_unknown_model_rejected(self) -> None:
        with pytest.raises(KernelError) as exc:
            sql_of([("name", "=", "x")], model="no.existe")
        assert exc.value.code == "MODEL_NOT_FOUND"

    def test_too_many_terms_rejected(self) -> None:
        domain: list[Any] = [("sequence", "=", i) for i in range(MAX_TERMS + 1)]
        with pytest.raises(KernelError) as exc:
            sql_of(domain)
        assert exc.value.code == "DOMAIN_TOO_LARGE"

    def test_malformed_term_rejected(self) -> None:
        for bad in ([("solo_dos", "=")], [("a", "b", "c", "d")], ["texto suelto"], [42]):
            with pytest.raises(KernelError):
                sql_of(bad)  # type: ignore[arg-type]


class TestInjection:
    """Bloqueantes: ningún dato del dominio puede llegar como SQL."""

    def test_injection_in_field_name(self) -> None:
        with pytest.raises(KernelError):
            sql_of([("name; DROP TABLE sale_order; --", "=", "x")])

    def test_injection_in_dotted_path(self) -> None:
        with pytest.raises(KernelError):
            sql_of([("partner_id.name'; DROP TABLE res_partner; --", "=", "x")])

    def test_injection_in_operator(self) -> None:
        with pytest.raises(KernelError):
            sql_of([("name", "= 1 OR 1=1 --", "x")])

    def test_injection_in_value_is_parameterized(self) -> None:
        payload = "x'; DROP TABLE sale_order; --"
        stmt = compiler().select(model="sale.order", domain=[("name", "=", payload)])
        compiled = stmt.compile()
        assert "DROP TABLE" not in str(compiled)
        assert payload in compiled.params.values()

    def test_injection_in_list_value_is_parameterized(self) -> None:
        payload = "y'; DELETE FROM res_partner; --"
        stmt = compiler().select(model="sale.order", domain=[("state", "in", [payload])])
        compiled = stmt.compile()
        assert "DELETE FROM" not in str(compiled)

    def test_injection_in_order_rejected(self) -> None:
        with pytest.raises(KernelError) as exc:
            compiler().select(model="sale.order", domain=[], order="id; DROP TABLE x")
        assert exc.value.code == "DOMAIN_INVALID_ORDER"

    def test_injection_in_requested_fields_rejected(self) -> None:
        with pytest.raises(KernelError) as exc:
            compiler().select(model="sale.order", domain=[], fields=["name, (SELECT 1)"])
        assert exc.value.code == "DOMAIN_UNKNOWN_FIELD"


class TestRecordRules:
    def test_global_rules_are_and(self) -> None:
        stmt = compiler().select(
            model="sale.order",
            domain=[("state", "=", "sale")],
            rules={"global_and": [[("sequence", ">", 0)], [("name", "!=", "")]]},
        )
        sql = str(stmt.compile()).upper()
        assert sql.count(" AND ") >= 2

    def test_role_rules_are_or(self) -> None:
        stmt = compiler().select(
            model="sale.order",
            domain=[],
            rules={"role_or": [[("sequence", ">", 5)], [("state", "=", "draft")]]},
        )
        assert " OR " in str(stmt.compile()).upper()

    def test_rules_cannot_be_bypassed_by_domain(self) -> None:
        """Aunque el dominio use OR, las reglas siguen en AND sobre el conjunto."""
        stmt = compiler().select(
            model="sale.order",
            domain=["|", ("state", "=", "sale"), ("state", "=", "draft")],
            rules={"global_and": [[("sequence", ">", 100)]]},
        )
        sql = str(stmt.compile()).upper()
        assert " OR " in sql
        assert " AND " in sql


class TestActiveTest:
    def test_active_filter_added_when_model_has_active(self) -> None:
        sql = sql_of([("name", "=", "ACME")], model="res.partner")
        assert "active" in sql.lower()

    def test_active_filter_can_be_disabled(self) -> None:
        stmt = compiler().select(model="res.partner", domain=[], active_test=False)
        assert "active" not in str(stmt.compile()).lower()

    def test_no_active_filter_when_field_absent(self) -> None:
        sql = sql_of([("name", "=", "SO1")])
        assert "active" not in sql.lower()
