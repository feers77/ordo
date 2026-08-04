"""Aislamiento entre tenants (F2-01, ADR-002): schema + RLS reales.

Estos tests son bloqueantes: una fuga entre tenants es el riesgo #1 del producto.
"""

import pytest
from ordo_core.environment import TENANT_GUC, Environment, schema_for
from ordo_core.errors import KernelError
from ordo_core.registry import Module, Registry
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

pytestmark = pytest.mark.integration

EMPTY_REGISTRY = Registry.build([Module("base")])


async def provision_tenant(session: AsyncSession, tenant: str) -> None:
    """Crea el schema del tenant con una tabla de negocio protegida por RLS."""
    schema = schema_for(tenant)
    await session.execute(text(f'CREATE SCHEMA IF NOT EXISTS "{schema}"'))
    await session.execute(
        text(
            f'CREATE TABLE IF NOT EXISTS "{schema}".res_partner ('
            "id serial PRIMARY KEY, tenant text NOT NULL, name text NOT NULL)"
        )
    )
    await session.execute(text(f'ALTER TABLE "{schema}".res_partner ENABLE ROW LEVEL SECURITY'))
    await session.execute(text(f'ALTER TABLE "{schema}".res_partner FORCE ROW LEVEL SECURITY'))
    await session.execute(text(f'GRANT USAGE ON SCHEMA "{schema}" TO ordo_app'))
    await session.execute(
        text(f'GRANT SELECT, INSERT, UPDATE, DELETE ON "{schema}".res_partner TO ordo_app')
    )
    await session.execute(
        text(f'GRANT USAGE, SELECT ON SEQUENCE "{schema}".res_partner_id_seq TO ordo_app')
    )
    await session.execute(text(f'DROP POLICY IF EXISTS tenant_isolation ON "{schema}".res_partner'))
    await session.execute(
        text(
            f'CREATE POLICY tenant_isolation ON "{schema}".res_partner '
            f"USING (tenant = current_setting('{TENANT_GUC}', true))"
        )
    )
    await session.commit()


class TestTenantIsolation:
    async def test_environment_binds_schema_and_guc(self, core_session: AsyncSession) -> None:
        await provision_tenant(core_session, "acme")
        env = Environment(session=core_session, tenant="acme", registry=EMPTY_REGISTRY)
        await env.bind()
        current_schema = await core_session.scalar(text("SELECT current_schema()"))
        current_tenant = await core_session.scalar(
            text(f"SELECT current_setting('{TENANT_GUC}', true)")
        )
        assert current_schema == "t_acme"
        assert current_tenant == "acme"
        await core_session.rollback()

    async def test_rls_blocks_writing_rows_of_other_tenant(
        self, core_session: AsyncSession
    ) -> None:
        """RLS no deja ni siquiera escribir una fila de otro tenant."""
        from sqlalchemy.exc import ProgrammingError

        await provision_tenant(core_session, "acme")
        env = Environment(session=core_session, tenant="acme", registry=EMPTY_REGISTRY)
        await env.bind()
        await core_session.execute(
            text("INSERT INTO res_partner (tenant, name) VALUES ('acme', 'Cliente Acme')")
        )
        with pytest.raises(ProgrammingError) as exc:
            await core_session.execute(
                text("INSERT INTO res_partner (tenant, name) VALUES ('globex', 'Ajeno')")
            )
        assert "row-level security" in str(exc.value)
        await core_session.rollback()

    async def test_rls_hides_rows_of_other_tenant(self, core_session: AsyncSession) -> None:
        """Filas ajenas insertadas por un rol privilegiado siguen invisibles."""
        await provision_tenant(core_session, "acme")
        # el rol dueño (superuser en dev) puede escribir cualquier fila
        await core_session.execute(
            text(
                'INSERT INTO "t_acme".res_partner (tenant, name) '
                "VALUES ('acme', 'Cliente Acme'), ('globex', 'Cliente Globex')"
            )
        )
        await core_session.commit()

        env = Environment(session=core_session, tenant="acme", registry=EMPTY_REGISTRY)
        await env.bind()
        visible = (await core_session.execute(text("SELECT name FROM res_partner"))).scalars().all()
        assert visible == ["Cliente Acme"]
        await core_session.rollback()

    async def test_each_tenant_sees_only_its_schema(self, core_session: AsyncSession) -> None:
        await provision_tenant(core_session, "acme")
        await provision_tenant(core_session, "globex")

        env_a = Environment(session=core_session, tenant="acme", registry=EMPTY_REGISTRY)
        await env_a.bind()
        await core_session.execute(
            text("INSERT INTO res_partner (tenant, name) VALUES ('acme', 'Solo Acme')")
        )
        await core_session.commit()

        env_b = Environment(session=core_session, tenant="globex", registry=EMPTY_REGISTRY)
        await env_b.bind()
        rows = (await core_session.execute(text("SELECT name FROM res_partner"))).scalars().all()
        assert rows == []
        await core_session.rollback()

    async def test_binding_survives_commit_and_rollback(self, core_session: AsyncSession) -> None:
        """El binding se re-aplica en cada transacción nueva de la sesión.

        Los ajustes son transaccionales: sin esto, un commit a mitad de un
        request dejaría las consultas siguientes sin filtro de tenant.
        """
        await provision_tenant(core_session, "acme")
        env = Environment(session=core_session, tenant="acme", registry=EMPTY_REGISTRY)
        await env.bind()
        await core_session.commit()
        after_commit = await core_session.scalar(
            text(f"SELECT current_setting('{TENANT_GUC}', true)")
        )
        assert after_commit == "acme"
        await core_session.rollback()
        after_rollback = await core_session.scalar(
            text(f"SELECT current_setting('{TENANT_GUC}', true)")
        )
        assert after_rollback == "acme"

    async def test_binding_does_not_leak_to_other_sessions(
        self, core_session: AsyncSession, core_db_url: str
    ) -> None:
        from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

        await provision_tenant(core_session, "acme")
        env = Environment(session=core_session, tenant="acme", registry=EMPTY_REGISTRY)
        await env.bind()

        engine = create_async_engine(core_db_url)
        maker = async_sessionmaker(engine, expire_on_commit=False)
        async with maker() as other:
            leaked = await other.scalar(text(f"SELECT current_setting('{TENANT_GUC}', true)"))
        await engine.dispose()
        assert leaked in (None, "")

    async def test_invalid_tenant_name_rejected(self, core_session: AsyncSession) -> None:
        for bad in ("Acme", "1acme", "acme; DROP SCHEMA public", "a", "acme-x"):
            with pytest.raises(KernelError) as exc:
                Environment(session=core_session, tenant=bad, registry=EMPTY_REGISTRY)
            assert exc.value.code == "TENANT_INVALID"
