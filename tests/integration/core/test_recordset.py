"""Tests del ORM de escritura (F2-04) — escritos antes de implementar."""

from decimal import Decimal

import pytest
from ordo_core.environment import Environment
from ordo_core.errors import KernelError
from ordo_core.fields import Boolean, Char, Monetary, Selection
from ordo_core.model import Model
from ordo_core.recordset import RecordSet
from ordo_core.registry import Module, Registry
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

pytestmark = pytest.mark.integration


def demo_registry() -> Registry:
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


async def make_env(session: AsyncSession, tenant: str) -> Environment:
    schema = f"t_{tenant}"
    await session.execute(text(f'CREATE SCHEMA IF NOT EXISTS "{schema}"'))
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
    env = Environment(session=session, tenant=tenant, registry=demo_registry(), app_role=None)
    await env.bind()
    return env


class TestCreateRead:
    async def test_create_batch_returns_ids(self, core_session: AsyncSession) -> None:
        env = await make_env(core_session, "rs1")
        rs = RecordSet(env, "res.partner")
        ids = await rs.create([{"name": "ACME"}, {"name": "Globex"}])
        assert len(ids) == 2
        rows = await rs.read(ids, fields=["name"])
        assert sorted(row["name"] for row in rows) == ["ACME", "Globex"]

    async def test_create_applies_defaults(self, core_session: AsyncSession) -> None:
        env = await make_env(core_session, "rs2")
        rs = RecordSet(env, "res.partner")
        [record_id] = await rs.create([{"name": "ACME"}])
        [row] = await rs.read([record_id], fields=["state", "version"])
        assert row["state"] == "draft"
        assert row["version"] == 1

    async def test_create_rejects_missing_required(self, core_session: AsyncSession) -> None:
        env = await make_env(core_session, "rs3")
        rs = RecordSet(env, "res.partner")
        with pytest.raises(KernelError) as exc:
            await rs.create([{"ref": "sin nombre"}])
        assert exc.value.code == "FIELD_REQUIRED"

    async def test_create_rejects_unknown_field(self, core_session: AsyncSession) -> None:
        env = await make_env(core_session, "rs4")
        rs = RecordSet(env, "res.partner")
        with pytest.raises(KernelError) as exc:
            await rs.create([{"name": "ACME", "fantasma": 1}])
        assert exc.value.code == "FIELD_UNKNOWN"

    async def test_create_rejects_invalid_selection(self, core_session: AsyncSession) -> None:
        env = await make_env(core_session, "rs5")
        rs = RecordSet(env, "res.partner")
        with pytest.raises(KernelError) as exc:
            await rs.create([{"name": "ACME", "state": "inventado"}])
        assert exc.value.code == "FIELD_INVALID_VALUE"

    async def test_monetary_rejects_float(self, core_session: AsyncSession) -> None:
        env = await make_env(core_session, "rs6")
        rs = RecordSet(env, "res.partner")
        with pytest.raises(KernelError) as exc:
            await rs.create([{"name": "ACME", "credit_limit": 1000.5}])
        assert exc.value.code == "FIELD_INVALID_VALUE"

    async def test_monetary_accepts_decimal_and_string(self, core_session: AsyncSession) -> None:
        env = await make_env(core_session, "rs7")
        rs = RecordSet(env, "res.partner")
        ids = await rs.create(
            [
                {"name": "A", "credit_limit": Decimal("1000.50")},
                {"name": "B", "credit_limit": "2000.25"},
            ]
        )
        rows = await rs.read(ids, fields=["credit_limit"])
        assert sorted(str(row["credit_limit"]) for row in rows) == ["1000.50", "2000.25"]


class TestWrite:
    async def test_write_updates_and_bumps_version(self, core_session: AsyncSession) -> None:
        env = await make_env(core_session, "rs8")
        rs = RecordSet(env, "res.partner")
        [record_id] = await rs.create([{"name": "ACME"}])
        await rs.write([record_id], {"ref": "C-001"})
        [row] = await rs.read([record_id], fields=["ref", "version"])
        assert row["ref"] == "C-001"
        assert row["version"] == 2

    async def test_write_rejects_readonly_field(self, core_session: AsyncSession) -> None:
        env = await make_env(core_session, "rs9")
        rs = RecordSet(env, "res.partner")
        [record_id] = await rs.create([{"name": "ACME"}])
        with pytest.raises(KernelError) as exc:
            await rs.write([record_id], {"version": 99})
        assert exc.value.code == "FIELD_READONLY"

    async def test_optimistic_lock_conflict(self, core_session: AsyncSession) -> None:
        env = await make_env(core_session, "rs10")
        rs = RecordSet(env, "res.partner")
        [record_id] = await rs.create([{"name": "ACME"}])
        await rs.write([record_id], {"ref": "primero"})
        with pytest.raises(KernelError) as exc:
            await rs.write([record_id], {"ref": "tarde"}, expected_version=1)
        assert exc.value.code == "CONCURRENT_MODIFICATION"
        assert exc.value.current_state is not None
        assert exc.value.current_state[0]["ref"] == "primero"

    async def test_optimistic_lock_success_with_right_version(
        self, core_session: AsyncSession
    ) -> None:
        env = await make_env(core_session, "rs11")
        rs = RecordSet(env, "res.partner")
        [record_id] = await rs.create([{"name": "ACME"}])
        await rs.write([record_id], {"ref": "ok"}, expected_version=1)
        [row] = await rs.read([record_id], fields=["ref"])
        assert row["ref"] == "ok"


class TestUnlinkAndSearch:
    async def test_unlink_removes_rows(self, core_session: AsyncSession) -> None:
        env = await make_env(core_session, "rs12")
        rs = RecordSet(env, "res.partner")
        ids = await rs.create([{"name": "A"}, {"name": "B"}])
        await rs.unlink([ids[0]])
        remaining = await rs.search([], fields=["name"])
        assert [row["name"] for row in remaining["rows"]] == ["B"]

    async def test_search_applies_domain(self, core_session: AsyncSession) -> None:
        env = await make_env(core_session, "rs13")
        rs = RecordSet(env, "res.partner")
        await rs.create([{"name": "ACME"}, {"name": "Globex"}])
        result = await rs.search([("name", "=", "ACME")], fields=["name"])
        assert [row["name"] for row in result["rows"]] == ["ACME"]

    async def test_cursor_pagination_is_stable(self, core_session: AsyncSession) -> None:
        env = await make_env(core_session, "rs14")
        rs = RecordSet(env, "res.partner")
        await rs.create([{"name": f"P{i:02d}"} for i in range(10)])
        first = await rs.search([], fields=["name"], limit=4)
        assert len(first["rows"]) == 4
        assert first["next_cursor"] is not None
        second = await rs.search([], fields=["name"], limit=4, cursor=first["next_cursor"])
        assert len(second["rows"]) == 4
        names_first = {row["name"] for row in first["rows"]}
        names_second = {row["name"] for row in second["rows"]}
        assert names_first.isdisjoint(names_second)

    async def test_cursor_ends_without_next(self, core_session: AsyncSession) -> None:
        env = await make_env(core_session, "rs15")
        rs = RecordSet(env, "res.partner")
        await rs.create([{"name": "solo"}])
        result = await rs.search([], fields=["name"], limit=10)
        assert result["next_cursor"] is None

    async def test_invalid_cursor_rejected(self, core_session: AsyncSession) -> None:
        env = await make_env(core_session, "rs16")
        rs = RecordSet(env, "res.partner")
        with pytest.raises(KernelError) as exc:
            await rs.search([], fields=["name"], cursor="no-es-un-cursor")
        assert exc.value.code == "INVALID_CURSOR"


class TestDryRun:
    async def test_dry_run_create_does_not_persist(self, core_session: AsyncSession) -> None:
        env = await make_env(core_session, "rs17")
        rs = RecordSet(env, "res.partner")
        result = await rs.create([{"name": "Fantasma"}], dry_run=True)
        assert result["would_create"] == 1
        assert result["validations"] == []
        remaining = await rs.search([], fields=["name"])
        assert remaining["rows"] == []

    async def test_dry_run_reports_validations(self, core_session: AsyncSession) -> None:
        env = await make_env(core_session, "rs18")
        rs = RecordSet(env, "res.partner")
        result = await rs.create([{"ref": "sin nombre"}], dry_run=True)
        assert result["validations"]
        assert result["validations"][0]["code"] == "FIELD_REQUIRED"

    async def test_dry_run_write_does_not_persist(self, core_session: AsyncSession) -> None:
        env = await make_env(core_session, "rs19")
        rs = RecordSet(env, "res.partner")
        [record_id] = await rs.create([{"name": "ACME"}])
        await rs.write([record_id], {"ref": "simulada"}, dry_run=True)
        [row] = await rs.read([record_id], fields=["ref", "version"])
        assert row["ref"] is None
        assert row["version"] == 1
