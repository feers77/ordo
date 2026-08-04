"""Tests de idempotencia y transacción multi-operación (F2-04)."""

import pytest
from ordo_core.errors import KernelError
from ordo_core.idempotency import create_table, remember, replay
from ordo_core.transactions import TransactionRunner
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from tests.integration.core.helpers import make_partner_env

pytestmark = pytest.mark.integration


class TestIdempotency:
    async def test_new_key_returns_none(self, core_session: AsyncSession) -> None:
        env = await make_partner_env(core_session, "idem1")
        await create_table(env.session)
        assert await replay(env.session, "k1", {"name": "ACME"}) is None

    async def test_same_key_same_payload_replays(self, core_session: AsyncSession) -> None:
        env = await make_partner_env(core_session, "idem2")
        await create_table(env.session)
        payload = {"name": "ACME"}
        await remember(env.session, "k2", payload, {"ids": [1]})
        assert await replay(env.session, "k2", payload) == {"ids": [1]}

    async def test_same_key_different_payload_conflicts(self, core_session: AsyncSession) -> None:
        env = await make_partner_env(core_session, "idem3")
        await create_table(env.session)
        await remember(env.session, "k3", {"name": "ACME"}, {"ids": [1]})
        with pytest.raises(KernelError) as exc:
            await replay(env.session, "k3", {"name": "Globex"})
        assert exc.value.code == "IDEMPOTENCY_KEY_REUSED"

    async def test_expired_entry_is_ignored(self, core_session: AsyncSession) -> None:
        env = await make_partner_env(core_session, "idem4")
        await create_table(env.session)
        await remember(env.session, "k4", {"name": "ACME"}, {"ids": [1]})
        await env.session.execute(
            text("UPDATE ir_idempotency SET expires_at = now() - interval '1 hour'")
        )
        assert await replay(env.session, "k4", {"name": "ACME"}) is None


class TestTransactionRunner:
    async def test_atomic_all_or_nothing(self, core_session: AsyncSession) -> None:
        env = await make_partner_env(core_session, "tx1")
        runner = TransactionRunner(env)
        with pytest.raises(KernelError):
            await runner.run(
                [
                    {"op": "create", "model": "res.partner", "values": {"name": "Válida"}},
                    {"op": "create", "model": "res.partner", "values": {"ref": "sin nombre"}},
                ],
                atomic=True,
            )
        remaining = await env.session.execute(text("SELECT count(*) FROM res_partner"))
        assert remaining.scalar() == 0

    async def test_atomic_success_persists_all(self, core_session: AsyncSession) -> None:
        env = await make_partner_env(core_session, "tx2")
        runner = TransactionRunner(env)
        results = await runner.run(
            [
                {"op": "create", "model": "res.partner", "values": {"name": "A"}},
                {"op": "create", "model": "res.partner", "values": {"name": "B"}},
            ],
            atomic=True,
        )
        assert len(results) == 2
        assert all(r["ok"] for r in results)
        count = await env.session.execute(text("SELECT count(*) FROM res_partner"))
        assert count.scalar() == 2

    async def test_non_atomic_reports_per_index(self, core_session: AsyncSession) -> None:
        env = await make_partner_env(core_session, "tx3")
        runner = TransactionRunner(env)
        results = await runner.run(
            [
                {"op": "create", "model": "res.partner", "values": {"name": "Válida"}},
                {"op": "create", "model": "res.partner", "values": {"ref": "falla"}},
                {"op": "create", "model": "res.partner", "values": {"name": "Otra"}},
            ],
            atomic=False,
        )
        assert [r["ok"] for r in results] == [True, False, True]
        assert results[1]["error"]["code"] == "FIELD_REQUIRED"
        count = await env.session.execute(text("SELECT count(*) FROM res_partner"))
        assert count.scalar() == 2

    async def test_write_and_unlink_operations(self, core_session: AsyncSession) -> None:
        env = await make_partner_env(core_session, "tx4")
        runner = TransactionRunner(env)
        created = await runner.run(
            [{"op": "create", "model": "res.partner", "values": {"name": "A"}}], atomic=True
        )
        record_id = created[0]["result"]["ids"][0]
        results = await runner.run(
            [
                {
                    "op": "write",
                    "model": "res.partner",
                    "ids": [record_id],
                    "values": {"ref": "R1"},
                },
                {"op": "unlink", "model": "res.partner", "ids": [record_id]},
            ],
            atomic=True,
        )
        assert all(r["ok"] for r in results)
        count = await env.session.execute(text("SELECT count(*) FROM res_partner"))
        assert count.scalar() == 0

    async def test_unknown_operation_rejected(self, core_session: AsyncSession) -> None:
        env = await make_partner_env(core_session, "tx5")
        runner = TransactionRunner(env)
        with pytest.raises(KernelError) as exc:
            await runner.run([{"op": "explode", "model": "res.partner"}], atomic=True)
        assert exc.value.code == "TX_UNKNOWN_OPERATION"
