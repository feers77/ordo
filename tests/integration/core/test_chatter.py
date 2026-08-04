"""Tests de chatter y adjuntos (F2-06)."""

from datetime import UTC, datetime, timedelta

import pytest
from ordo_core.errors import KernelError
from ordo_core.services.attachments import AttachmentService, InMemoryStorage
from ordo_core.services.chatter import Chatter
from ordo_core.services.schema import create_kernel_tables
from sqlalchemy.ext.asyncio import AsyncSession

from tests.integration.core.helpers import make_partner_env

pytestmark = pytest.mark.integration


async def setup(session: AsyncSession, tenant: str) -> None:
    await make_partner_env(session, tenant)
    await create_kernel_tables(session)
    await session.commit()


class TestChatterMessages:
    async def test_post_and_read_thread(self, core_session: AsyncSession) -> None:
        await setup(core_session, "chat1")
        chatter = Chatter(core_session)
        await chatter.post(model="res.partner", res_id=1, body="Primer mensaje", author_kind="user")
        await chatter.post(
            model="res.partner", res_id=1, body="Respuesta del agente", author_kind="agent"
        )
        thread = await chatter.thread("res.partner", 1)
        assert [m["body"] for m in thread] == ["Primer mensaje", "Respuesta del agente"]

    async def test_author_kind_distinguishes_agent_from_human(
        self, core_session: AsyncSession
    ) -> None:
        await setup(core_session, "chat2")
        chatter = Chatter(core_session)
        await chatter.post(
            model="res.partner",
            res_id=1,
            body="Lo hice yo",
            author_kind="agent",
            author_principal="agent:abc",
        )
        [message] = await chatter.thread("res.partner", 1)
        assert message["author_kind"] == "agent"
        assert message["author_principal"] == "agent:abc"

    async def test_invalid_author_kind_rejected(self, core_session: AsyncSession) -> None:
        await setup(core_session, "chat3")
        with pytest.raises(KernelError) as exc:
            await Chatter(core_session).post(
                model="res.partner", res_id=1, body="x", author_kind="robot"
            )
        assert exc.value.code == "CHATTER_INVALID_AUTHOR_KIND"

    async def test_thread_is_scoped_to_record(self, core_session: AsyncSession) -> None:
        await setup(core_session, "chat4")
        chatter = Chatter(core_session)
        await chatter.post(model="res.partner", res_id=1, body="Del uno", author_kind="user")
        await chatter.post(model="res.partner", res_id=2, body="Del dos", author_kind="user")
        assert [m["body"] for m in await chatter.thread("res.partner", 1)] == ["Del uno"]

    async def test_tracking_records_old_and_new_values(self, core_session: AsyncSession) -> None:
        await setup(core_session, "chat5")
        chatter = Chatter(core_session)
        await chatter.track_changes(
            model="res.partner",
            res_id=1,
            changes={"state": ("draft", "active")},
            author_principal="agent:abc",
            author_kind="agent",
        )
        [message] = await chatter.thread("res.partner", 1)
        assert message["message_type"] == "tracking"
        assert "draft" in message["body"]
        assert "active" in message["body"]

    async def test_tracking_without_changes_posts_nothing(self, core_session: AsyncSession) -> None:
        await setup(core_session, "chat6")
        chatter = Chatter(core_session)
        assert await chatter.track_changes(model="res.partner", res_id=1, changes={}) is None
        assert await chatter.thread("res.partner", 1) == []


class TestFollowers:
    async def test_follow_is_idempotent(self, core_session: AsyncSession) -> None:
        await setup(core_session, "foll1")
        chatter = Chatter(core_session)
        await chatter.follow("res.partner", 1, "user:1")
        await chatter.follow("res.partner", 1, "user:1")
        assert await chatter.followers("res.partner", 1) == ["user:1"]

    async def test_unfollow_removes(self, core_session: AsyncSession) -> None:
        await setup(core_session, "foll2")
        chatter = Chatter(core_session)
        await chatter.follow("res.partner", 1, "user:1")
        await chatter.follow("res.partner", 1, "agent:2")
        await chatter.unfollow("res.partner", 1, "user:1")
        assert await chatter.followers("res.partner", 1) == ["agent:2"]


class TestActivities:
    async def test_state_derives_from_deadline(self, core_session: AsyncSession) -> None:
        await setup(core_session, "act1")
        chatter = Chatter(core_session)
        today = datetime.now(UTC).date()
        await chatter.schedule_activity(
            model="res.partner",
            res_id=1,
            summary="Vencida",
            assigned_to="user:1",
            date_deadline=today - timedelta(days=1),
        )
        await chatter.schedule_activity(
            model="res.partner",
            res_id=1,
            summary="Hoy",
            assigned_to="user:1",
            date_deadline=today,
        )
        await chatter.schedule_activity(
            model="res.partner",
            res_id=1,
            summary="Futura",
            assigned_to="user:1",
            date_deadline=today + timedelta(days=3),
        )
        states = {a["summary"]: a["state"] for a in await chatter.activities("res.partner", 1)}
        assert states == {"Vencida": "overdue", "Hoy": "today", "Futura": "planned"}

    async def test_completed_activity_disappears(self, core_session: AsyncSession) -> None:
        await setup(core_session, "act2")
        chatter = Chatter(core_session)
        activity_id = await chatter.schedule_activity(
            model="res.partner",
            res_id=1,
            summary="Llamar",
            assigned_to="user:1",
            date_deadline=datetime.now(UTC).date(),
        )
        await chatter.complete_activity(activity_id)
        assert await chatter.activities("res.partner", 1) == []


class TestAttachments:
    async def test_upload_and_download(self, core_session: AsyncSession) -> None:
        await setup(core_session, "att1")
        service = AttachmentService(core_session, InMemoryStorage())
        result = await service.upload(name="nota.txt", data=b"contenido")
        assert result["file_size"] == 9
        assert await service.download(result["id"]) == b"contenido"

    async def test_identical_content_is_deduplicated(self, core_session: AsyncSession) -> None:
        await setup(core_session, "att2")
        storage = InMemoryStorage()
        service = AttachmentService(core_session, storage)
        first = await service.upload(name="a.txt", data=b"mismo")
        second = await service.upload(name="b.txt", data=b"mismo")
        assert first["checksum"] == second["checksum"]
        assert second["deduplicated"] is True
        assert len(storage.objects) == 1  # un solo objeto para dos adjuntos

    async def test_deleting_one_keeps_bytes_of_the_other(self, core_session: AsyncSession) -> None:
        await setup(core_session, "att3")
        storage = InMemoryStorage()
        service = AttachmentService(core_session, storage)
        first = await service.upload(name="a.txt", data=b"compartido")
        second = await service.upload(name="b.txt", data=b"compartido")
        await service.delete(first["id"])
        assert await service.download(second["id"]) == b"compartido"

    async def test_deleting_last_reference_removes_object(self, core_session: AsyncSession) -> None:
        await setup(core_session, "att4")
        storage = InMemoryStorage()
        service = AttachmentService(core_session, storage)
        only = await service.upload(name="a.txt", data=b"unico")
        await service.delete(only["id"])
        assert storage.objects == {}

    async def test_checksum_is_derived_from_content(self, core_session: AsyncSession) -> None:
        import hashlib

        await setup(core_session, "att5")
        service = AttachmentService(core_session, InMemoryStorage())
        data = b"verificable"
        result = await service.upload(name="x", data=data)
        assert result["checksum"] == hashlib.sha256(data).hexdigest()

    async def test_attachments_listed_per_record(self, core_session: AsyncSession) -> None:
        await setup(core_session, "att6")
        service = AttachmentService(core_session, InMemoryStorage())
        await service.upload(name="uno.txt", data=b"1", model="res.partner", res_id=7)
        await service.upload(name="dos.txt", data=b"2", model="res.partner", res_id=7)
        await service.upload(name="otro.txt", data=b"3", model="res.partner", res_id=8)
        listed = await service.for_record("res.partner", 7)
        assert [a["name"] for a in listed] == ["uno.txt", "dos.txt"]

    async def test_missing_attachment_rejected(self, core_session: AsyncSession) -> None:
        await setup(core_session, "att7")
        service = AttachmentService(core_session, InMemoryStorage())
        with pytest.raises(KernelError) as exc:
            await service.download(999)
        assert exc.value.code == "ATTACHMENT_NOT_FOUND"
