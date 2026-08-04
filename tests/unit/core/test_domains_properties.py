"""Property-based testing del compilador de dominios (F2-02, PLAN §9).

Invariante central: ningún valor del dominio aparece nunca como literal
en el SQL generado; siempre viaja como parámetro vinculado.
"""

from typing import Any

from hypothesis import given, settings
from hypothesis import strategies as st
from ordo_core.domains import DomainCompiler
from ordo_core.errors import KernelError
from ordo_core.fields import Boolean, Char, Integer, Many2one
from ordo_core.model import Model
from ordo_core.registry import Module, Registry


def _registry() -> Registry:
    class Partner(Model):
        _name = "res.partner"
        _description = "Contacto"

        name = Char(agent_hint="Nombre", examples=["ACME"])
        active = Boolean(agent_hint="Activo", examples=["true"])

    class SaleOrder(Model):
        _name = "sale.order"
        _description = "Orden"

        name = Char(agent_hint="Número", examples=["SO1"])
        state = Char(agent_hint="Estado", examples=["draft"])
        sequence = Integer(agent_hint="Secuencia", examples=["1"])
        partner_id = Many2one("res.partner", agent_hint="Cliente", examples=["1"])

    return Registry.build([Module("demo", models=[Partner, SaleOrder])])


REGISTRY = _registry()
# Derivado del registry, no hardcodeado: si el modelo gana un campo, el test
# no empieza a fallar por casualidad (write_uid lo destapó).
VALID_FIELDS = set(REGISTRY["sale.order"].fields)

text_values = st.text(min_size=0, max_size=40)
int_values = st.integers(min_value=-10_000, max_value=10_000)


def _term() -> st.SearchStrategy[tuple[str, str, Any]]:
    return st.one_of(
        st.tuples(
            st.sampled_from(["name", "state", "partner_id.name"]),
            st.sampled_from(["=", "!=", "like", "ilike"]),
            text_values,
        ),
        st.tuples(st.just("sequence"), st.sampled_from(["=", ">", "<", ">=", "<="]), int_values),
        st.tuples(
            st.sampled_from(["state", "name"]), st.just("in"), st.lists(text_values, max_size=4)
        ),
    )


def _domain() -> st.SearchStrategy[list[Any]]:
    return st.recursive(
        st.lists(_term(), min_size=0, max_size=3),
        lambda children: st.builds(
            lambda op, a, b: [op, *a, *b],
            st.sampled_from(["|", "&"]),
            children.filter(lambda d: len(d) == 1),
            children.filter(lambda d: len(d) == 1),
        ),
        max_leaves=6,
    )


@settings(max_examples=200, deadline=None)
@given(domain=_domain())
def test_valid_domains_always_compile(domain: list[Any]) -> None:
    compiler = DomainCompiler(REGISTRY, schema="t_acme")
    try:
        stmt = compiler.select(model="sale.order", domain=domain)
    except KernelError:
        return  # rechazo explícito también es comportamiento correcto
    assert stmt is not None


@settings(max_examples=200, deadline=None)
@given(value=text_values.filter(lambda v: len(v) > 2))
def test_values_never_appear_as_literals(value: str) -> None:
    compiler = DomainCompiler(REGISTRY, schema="t_acme")
    stmt = compiler.select(model="sale.order", domain=[("name", "=", value)])
    compiled = stmt.compile()
    assert value not in str(compiled)
    assert value in compiled.params.values()


@settings(max_examples=100, deadline=None)
@given(field=st.text(min_size=1, max_size=20).filter(lambda f: f not in VALID_FIELDS))
def test_unknown_fields_always_rejected(field: str) -> None:
    compiler = DomainCompiler(REGISTRY, schema="t_acme")
    try:
        compiler.select(model="sale.order", domain=[(field, "=", "x")])
    except KernelError as exc:
        assert exc.code in {
            "DOMAIN_UNKNOWN_FIELD",
            "DOMAIN_INVALID_PATH",
            "DOMAIN_PATH_TOO_DEEP",
            "MODEL_NOT_FOUND",
        }
    else:  # pragma: no cover
        msg = f"campo desconocido aceptado: {field!r}"
        raise AssertionError(msg)


@settings(max_examples=100, deadline=None)
@given(operator=st.text(min_size=1, max_size=10))
def test_unknown_operators_always_rejected(operator: str) -> None:
    from ordo_core.domains import COMPARISON_OPERATORS

    if operator in COMPARISON_OPERATORS:
        return
    compiler = DomainCompiler(REGISTRY, schema="t_acme")
    try:
        compiler.select(model="sale.order", domain=[("name", operator, "x")])
    except KernelError as exc:
        assert exc.code in {"DOMAIN_UNKNOWN_OPERATOR", "DOMAIN_MALFORMED"}
    else:  # pragma: no cover
        msg = f"operador desconocido aceptado: {operator!r}"
        raise AssertionError(msg)
