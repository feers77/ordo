"""El SQL generado por el compilador se ejecuta de verdad contra Postgres (F2-02).

Compilar sin excepción no prueba nada: aquí se verifica que el SQL es válido
y que devuelve exactamente las filas esperadas, con RLS activo.
"""

import pytest
from ordo_core.domains import DomainCompiler
from ordo_core.environment import Environment
from ordo_core.fields import Boolean, Char, Integer, Many2one
from ordo_core.model import Model
from ordo_core.registry import Module, Registry
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

pytestmark = pytest.mark.integration


def demo_registry() -> Registry:
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
        _description = "Orden"

        name = Char(agent_hint="Número", examples=["SO1"])
        state = Char(agent_hint="Estado", examples=["draft"])
        sequence = Integer(agent_hint="Secuencia", examples=["1"])
        partner_id = Many2one("res.partner", agent_hint="Cliente", examples=["1"])

    return Registry.build([Module("demo", models=[Country, Partner, SaleOrder])])


async def setup_schema(session: AsyncSession, tenant: str) -> Environment:
    schema = f"t_{tenant}"
    await session.execute(text(f'CREATE SCHEMA IF NOT EXISTS "{schema}"'))
    await session.execute(
        text(
            f'CREATE TABLE IF NOT EXISTS "{schema}".res_country (id serial PRIMARY KEY, code text)'
        )
    )
    await session.execute(
        text(
            f'CREATE TABLE IF NOT EXISTS "{schema}".res_partner '
            "(id serial PRIMARY KEY, name text, active boolean DEFAULT true, "
            "country_id integer)"
        )
    )
    await session.execute(
        text(
            f'CREATE TABLE IF NOT EXISTS "{schema}".sale_order '
            "(id serial PRIMARY KEY, name text, state text, sequence integer, "
            "partner_id integer)"
        )
    )
    await session.execute(
        text(
            f"INSERT INTO \"{schema}\".res_country (id, code) VALUES (1,'CL'),(2,'AR') "
            "ON CONFLICT DO NOTHING"
        )
    )
    await session.execute(
        text(
            f'INSERT INTO "{schema}".res_partner (id, name, active, country_id) VALUES '
            "(1,'ACME',true,1),(2,'Globex',true,2),(3,'Inactiva',false,1) "
            "ON CONFLICT DO NOTHING"
        )
    )
    await session.execute(
        text(
            f'INSERT INTO "{schema}".sale_order (id, name, state, sequence, partner_id) VALUES '
            "(1,'SO0001','sale',10,1),(2,'SO0002','draft',20,2),(3,'SO0003','sale',30,1) "
            "ON CONFLICT DO NOTHING"
        )
    )
    await session.commit()
    env = Environment(session=session, tenant=tenant, registry=demo_registry(), app_role=None)
    await env.bind()
    return env


async def ids(session: AsyncSession, stmt: object) -> list[int]:
    result = await session.execute(stmt)  # type: ignore[arg-type]
    return sorted(row[0] for row in result.all())


class TestExecutedSQL:
    async def test_simple_filter(self, core_session: AsyncSession) -> None:
        env = await setup_schema(core_session, "dom1")
        compiler = DomainCompiler(env.registry, schema=env.schema)
        stmt = compiler.select(model="sale.order", domain=[("state", "=", "sale")])
        assert await ids(core_session, stmt) == [1, 3]

    async def test_or_and_combination(self, core_session: AsyncSession) -> None:
        env = await setup_schema(core_session, "dom2")
        compiler = DomainCompiler(env.registry, schema=env.schema)
        stmt = compiler.select(
            model="sale.order",
            domain=["|", ("state", "=", "draft"), ("sequence", ">", 25)],
        )
        assert await ids(core_session, stmt) == [2, 3]

    async def test_dotted_path_join_executes(self, core_session: AsyncSession) -> None:
        env = await setup_schema(core_session, "dom3")
        compiler = DomainCompiler(env.registry, schema=env.schema)
        stmt = compiler.select(model="sale.order", domain=[("partner_id.name", "=", "ACME")])
        assert await ids(core_session, stmt) == [1, 3]

    async def test_two_hop_path_executes(self, core_session: AsyncSession) -> None:
        env = await setup_schema(core_session, "dom4")
        compiler = DomainCompiler(env.registry, schema=env.schema)
        stmt = compiler.select(
            model="sale.order", domain=[("partner_id.country_id.code", "=", "AR")]
        )
        assert await ids(core_session, stmt) == [2]

    async def test_in_and_like(self, core_session: AsyncSession) -> None:
        env = await setup_schema(core_session, "dom5")
        compiler = DomainCompiler(env.registry, schema=env.schema)
        stmt = compiler.select(model="sale.order", domain=[("name", "in", ["SO0001", "SO0002"])])
        assert await ids(core_session, stmt) == [1, 2]
        stmt = compiler.select(model="sale.order", domain=[("name", "like", "0003")])
        assert await ids(core_session, stmt) == [3]

    async def test_active_test_filters_inactive(self, core_session: AsyncSession) -> None:
        env = await setup_schema(core_session, "dom6")
        compiler = DomainCompiler(env.registry, schema=env.schema)
        stmt = compiler.select(model="res.partner", domain=[])
        assert await ids(core_session, stmt) == [1, 2]
        stmt = compiler.select(model="res.partner", domain=[], active_test=False)
        assert await ids(core_session, stmt) == [1, 2, 3]

    async def test_record_rules_restrict_results(self, core_session: AsyncSession) -> None:
        env = await setup_schema(core_session, "dom7")
        compiler = DomainCompiler(env.registry, schema=env.schema)
        stmt = compiler.select(
            model="sale.order",
            domain=[],
            rules={"global_and": [[("sequence", ">", 15)]]},
        )
        assert await ids(core_session, stmt) == [2, 3]

    async def test_role_rules_are_or_of_alternatives(self, core_session: AsyncSession) -> None:
        env = await setup_schema(core_session, "dom8")
        compiler = DomainCompiler(env.registry, schema=env.schema)
        stmt = compiler.select(
            model="sale.order",
            domain=[],
            rules={"role_or": [[("sequence", "=", 10)], [("state", "=", "draft")]]},
        )
        assert await ids(core_session, stmt) == [1, 2]

    async def test_domain_cannot_escape_record_rules(self, core_session: AsyncSession) -> None:
        """Aunque el dominio pida todo, la regla sigue acotando el resultado."""
        env = await setup_schema(core_session, "dom9")
        compiler = DomainCompiler(env.registry, schema=env.schema)
        stmt = compiler.select(
            model="sale.order",
            domain=["|", ("sequence", ">", 0), ("sequence", "<", 0)],
            rules={"global_and": [[("state", "=", "sale")]]},
        )
        assert await ids(core_session, stmt) == [1, 3]

    async def test_injection_payload_is_stored_as_data_not_executed(
        self, core_session: AsyncSession
    ) -> None:
        env = await setup_schema(core_session, "dom10")
        compiler = DomainCompiler(env.registry, schema=env.schema)
        payload = "'; DROP TABLE sale_order; --"
        stmt = compiler.select(model="sale.order", domain=[("name", "=", payload)])
        assert await ids(core_session, stmt) == []
        still_there = await core_session.scalar(text("SELECT count(*) FROM sale_order"))
        assert still_there == 3

    async def test_limit_offset_and_order(self, core_session: AsyncSession) -> None:
        env = await setup_schema(core_session, "dom11")
        compiler = DomainCompiler(env.registry, schema=env.schema)
        stmt = compiler.select(model="sale.order", domain=[], order="sequence desc", limit=2)
        result = await core_session.execute(stmt)
        assert [row[0] for row in result.all()] == [3, 2]
