"""Helpers compartidos por los tests de integración del kernel."""

from ordo_core.environment import Environment
from ordo_core.fields import Boolean, Char, Monetary, Selection
from ordo_core.model import Model
from ordo_core.registry import Module, Registry
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


def partner_registry() -> Registry:
    class Partner(Model):
        _name = "res.partner"
        _description = "Contacto"

        name = Char(required=True, agent_hint="Nombre", examples=["ACME"])
        ref = Char(agent_hint="Referencia interna", examples=["C-001"])
        credit_limit = Monetary(agent_hint="Límite de crédito", examples=["1000000.00"])
        state = Selection(
            [("draft", "Borrador"), ("active", "Activo")],
            default="draft",
            agent_hint="Estado",
            examples=["draft"],
        )
        active = Boolean(default=True, agent_hint="Activo", examples=["true"])

    return Registry.build([Module("demo", models=[Partner])])


async def make_partner_env(session: AsyncSession, tenant: str) -> Environment:
    from ordo_core.idempotency import create_table as create_idempotency_table

    schema = f"t_{tenant}"
    await session.execute(text(f'CREATE SCHEMA IF NOT EXISTS "{schema}"'))
    await session.execute(text(f"SELECT set_config('search_path', '{schema},public', false)"))
    await create_idempotency_table(session)
    await session.execute(
        text(
            f'CREATE TABLE IF NOT EXISTS "{schema}".res_partner ('
            "id serial PRIMARY KEY, name text NOT NULL, ref text, "
            "credit_limit numeric(18,2), state text DEFAULT 'draft', "
            "active boolean DEFAULT true, create_uid integer, "
            "create_date timestamptz DEFAULT now(), write_uid integer, "
            "write_date timestamptz DEFAULT now(), version integer DEFAULT 1)"
        )
    )
    await session.commit()
    env = Environment(session=session, tenant=tenant, registry=partner_registry(), app_role=None)
    await env.bind()
    return env
