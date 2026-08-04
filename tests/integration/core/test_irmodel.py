"""Persistencia de metadatos del registry en ir_model / ir_model_field (F2-01)."""

import pytest
from ordo_core.environment import Environment
from ordo_core.fields import Char, Many2one
from ordo_core.irmodel import sync_registry
from ordo_core.model import Model
from ordo_core.registry import Module, Registry
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

pytestmark = pytest.mark.integration


def demo_registry() -> Registry:
    class Partner(Model):
        _name = "res.partner"
        _description = "Contacto"

        name = Char(required=True, agent_hint="Nombre del contacto", examples=["ACME SpA"])

    class SaleOrder(Model):
        _name = "sale.order"
        _description = "Orden de venta"

        name = Char(required=True, agent_hint="Número del documento", examples=["SO0001"])
        partner_id = Many2one("res.partner", agent_hint="Cliente", examples=["42"])

    return Registry.build([Module("demo", models=[Partner, SaleOrder])])


async def bind_admin(session: AsyncSession, tenant: str) -> Environment:
    """Environment sin cambio de rol: crear metadatos requiere DDL."""
    registry = demo_registry()
    await session.execute(text(f'CREATE SCHEMA IF NOT EXISTS "t_{tenant}"'))
    env = Environment(session=session, tenant=tenant, registry=registry, app_role=None)
    await env.bind()
    return env


class TestIrModelSync:
    async def test_models_and_fields_persisted(self, core_session: AsyncSession) -> None:
        env = await bind_admin(core_session, "meta1")
        await sync_registry(core_session, env.registry)
        await env.bind()

        models = (
            (await core_session.execute(text("SELECT name FROM ir_model ORDER BY name")))
            .scalars()
            .all()
        )
        assert models == ["res.partner", "sale.order"]

        spec = await core_session.scalar(
            text("SELECT spec FROM ir_model_field WHERE model='sale.order' AND name='partner_id'")
        )
        assert spec["type"] == "many2one"
        assert spec["comodel"] == "res.partner"
        assert spec["agent_hint"] == "Cliente"

    async def test_sync_is_idempotent(self, core_session: AsyncSession) -> None:
        env = await bind_admin(core_session, "meta2")
        await sync_registry(core_session, env.registry)
        await sync_registry(core_session, env.registry)
        await env.bind()
        count = await core_session.scalar(text("SELECT count(*) FROM ir_model"))
        assert count == 2

    async def test_metadata_is_per_tenant(self, core_session: AsyncSession) -> None:
        env_a = await bind_admin(core_session, "meta3")
        await sync_registry(core_session, env_a.registry)
        env_b = await bind_admin(core_session, "meta4")
        await env_b.bind()
        exists = await core_session.scalar(
            text("SELECT to_regclass('t_meta4.ir_model') IS NOT NULL")
        )
        assert exists is False
