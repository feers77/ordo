"""Tests de secuencias, jobs, cron y outbox (F2-05)."""

import asyncio
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from ordo_core.errors import KernelError
from ordo_core.services import JobQueue, Outbox, SequenceService
from ordo_core.services.schema import create_kernel_tables
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from tests.integration.core.helpers import make_partner_env

pytestmark = pytest.mark.integration


async def setup(session: AsyncSession, tenant: str) -> AsyncSession:
    await make_partner_env(session, tenant)
    await create_kernel_tables(session)
    await session.commit()
    return session


class TestSequences:
    async def test_format_with_prefix_and_padding(self, core_session: AsyncSession) -> None:
        await setup(core_session, "seq1")
        service = SequenceService(core_session)
        await service.create(code="sale.order", name="Ventas", prefix="SO", padding=5)
        assert await service.next_by_code("sale.order") == "SO00001"
        assert await service.next_by_code("sale.order") == "SO00002"

    async def test_unknown_sequence_rejected(self, core_session: AsyncSession) -> None:
        await setup(core_session, "seq2")
        with pytest.raises(KernelError) as exc:
            await SequenceService(core_session).next_by_code("no.existe")
        assert exc.value.code == "SEQUENCE_NOT_FOUND"

    async def test_no_gap_has_no_holes_under_concurrency(
        self, core_session: AsyncSession, core_db_url: str
    ) -> None:
        """Dos sesiones concurrentes no pueden saltarse un número."""
        await setup(core_session, "seq3")
        await SequenceService(core_session).create(
            code="account.move", name="Asientos", prefix="F", padding=4, implementation="no_gap"
        )
        await core_session.commit()

        engine = create_async_engine(core_db_url)
        maker = async_sessionmaker(engine, expire_on_commit=False)

        async def take() -> str:
            async with maker() as session:
                await session.execute(text('SET search_path TO "t_seq3", public'))
                number = await SequenceService(session).next_by_code("account.move")
                await asyncio.sleep(0.05)  # ventana para provocar la carrera
                await session.commit()
                return number

        numbers = await asyncio.gather(*(take() for _ in range(5)))
        await engine.dispose()
        assert sorted(numbers) == ["F0001", "F0002", "F0003", "F0004", "F0005"]


class TestJobQueue:
    async def test_enqueue_and_claim(self, core_session: AsyncSession) -> None:
        await setup(core_session, "job1")
        queue = JobQueue(core_session)
        await queue.enqueue("send_email", {"to": "a@b.cl"})
        claimed = await queue.claim("worker-1")
        assert len(claimed) == 1
        assert claimed[0]["name"] == "send_email"
        assert claimed[0]["payload"] == {"to": "a@b.cl"}

    async def test_skip_locked_prevents_double_claim(
        self, core_session: AsyncSession, core_db_url: str
    ) -> None:
        await setup(core_session, "job2")
        await JobQueue(core_session).enqueue("solo_una_vez")
        await core_session.commit()

        engine = create_async_engine(core_db_url)
        maker = async_sessionmaker(engine, expire_on_commit=False)

        async def worker(name: str) -> list[dict[str, Any]]:
            async with maker() as session:
                await session.execute(text('SET search_path TO "t_job2", public'))
                claimed = await JobQueue(session).claim(name)
                await asyncio.sleep(0.05)
                await session.commit()
                return claimed

        results = await asyncio.gather(worker("w1"), worker("w2"))
        await engine.dispose()
        total = sum(len(r) for r in results)
        assert total == 1  # exactamente un worker se lo llevó

    async def test_failure_reschedules_with_backoff(self, core_session: AsyncSession) -> None:
        await setup(core_session, "job3")
        queue = JobQueue(core_session)
        job_id = await queue.enqueue("falla", max_attempts=3)
        await queue.claim("w")
        state = await queue.fail(job_id, "boom")
        assert state == "pending"
        row = (
            await core_session.execute(
                text("SELECT state, run_at, last_error FROM ir_job WHERE id = :id"),
                {"id": job_id},
            )
        ).first()
        assert row is not None
        assert row.state == "pending"
        assert row.run_at > datetime.now(UTC)
        assert row.last_error == "boom"

    async def test_exhausted_attempts_go_to_dlq(self, core_session: AsyncSession) -> None:
        await setup(core_session, "job4")
        queue = JobQueue(core_session)
        job_id = await queue.enqueue("condenado", max_attempts=1)
        await queue.claim("w")
        assert await queue.fail(job_id, "sin remedio") == "dead"
        state = await core_session.scalar(
            text("SELECT state FROM ir_job WHERE id = :id"), {"id": job_id}
        )
        assert state == "dead"

    async def test_job_from_aborted_transaction_does_not_exist(
        self, core_session: AsyncSession
    ) -> None:
        """Encolar es transaccional: sin commit no hay job (ADR-007)."""
        await setup(core_session, "job5")
        await JobQueue(core_session).enqueue("fantasma")
        await core_session.rollback()
        count = await core_session.scalar(text("SELECT count(*) FROM ir_job"))
        assert count == 0

    async def test_completed_job_is_not_reclaimed(self, core_session: AsyncSession) -> None:
        await setup(core_session, "job6")
        queue = JobQueue(core_session)
        job_id = await queue.enqueue("una_vez")
        await queue.claim("w")
        await queue.complete(job_id)
        assert await queue.claim("w") == []


class TestCron:
    async def test_due_cron_enqueues_job(self, core_session: AsyncSession) -> None:
        await setup(core_session, "cron1")
        queue = JobQueue(core_session)
        await queue.schedule_cron(name="limpieza", job_name="cleanup", interval_seconds=3600)
        created = await queue.run_due_crons()
        assert len(created) == 1
        claimed = await queue.claim("w")
        assert claimed[0]["name"] == "cleanup"

    async def test_not_due_cron_is_skipped(self, core_session: AsyncSession) -> None:
        await setup(core_session, "cron2")
        queue = JobQueue(core_session)
        await queue.schedule_cron(
            name="futuro",
            job_name="cleanup",
            interval_seconds=3600,
            next_call=datetime.now(UTC) + timedelta(hours=1),
        )
        assert await queue.run_due_crons() == []

    async def test_next_call_advances(self, core_session: AsyncSession) -> None:
        await setup(core_session, "cron3")
        queue = JobQueue(core_session)
        await queue.schedule_cron(name="ciclica", job_name="tick", interval_seconds=60)
        await queue.run_due_crons()
        next_call = await core_session.scalar(
            text("SELECT next_call FROM ir_cron WHERE name='ciclica'")
        )
        assert next_call > datetime.now(UTC)
        assert await queue.run_due_crons() == []  # ya no está vencida


class TestOutbox:
    async def test_event_written_with_business_change(self, core_session: AsyncSession) -> None:
        await setup(core_session, "out1")
        outbox = Outbox(core_session)
        await outbox.emit("sale.order.confirmed", "ordo.sale.order", {"id": 1})
        pending = await outbox.pending()
        assert len(pending) == 1
        assert pending[0]["subject"] == "ordo.sale.order"

    async def test_rollback_leaves_no_event(self, core_session: AsyncSession) -> None:
        await setup(core_session, "out2")
        await Outbox(core_session).emit("x", "s", {"a": 1})
        await core_session.rollback()
        count = await core_session.scalar(text("SELECT count(*) FROM ir_outbox"))
        assert count == 0

    async def test_relay_publishes_and_marks(self, core_session: AsyncSession) -> None:
        await setup(core_session, "out3")
        outbox = Outbox(core_session)
        await outbox.emit("a", "ordo.a", {"n": 1})
        await outbox.emit("b", "ordo.b", {"n": 2})
        sent: list[tuple[str, str, dict[str, Any]]] = []

        async def publish(subject: str, msg_id: str, payload: dict[str, Any]) -> None:
            sent.append((subject, msg_id, payload))

        assert await outbox.relay(publish) == 2
        assert [s[0] for s in sent] == ["ordo.a", "ordo.b"]
        assert await outbox.pending() == []

    async def test_relay_twice_does_not_republish(self, core_session: AsyncSession) -> None:
        await setup(core_session, "out4")
        outbox = Outbox(core_session)
        await outbox.emit("a", "ordo.a", {"n": 1})
        calls: list[str] = []

        async def publish(subject: str, msg_id: str, payload: dict[str, Any]) -> None:
            calls.append(msg_id)

        await outbox.relay(publish)
        await outbox.relay(publish)
        assert len(calls) == 1

    async def test_message_id_is_outbox_id(self, core_session: AsyncSession) -> None:
        """El id del mensaje permite al broker deduplicar tras un crash del relay."""
        await setup(core_session, "out5")
        outbox = Outbox(core_session)
        event_id = await outbox.emit("a", "ordo.a", {"n": 1})
        seen: list[str] = []

        async def publish(subject: str, msg_id: str, payload: dict[str, Any]) -> None:
            seen.append(msg_id)

        await outbox.relay(publish)
        assert seen == [str(event_id)]
