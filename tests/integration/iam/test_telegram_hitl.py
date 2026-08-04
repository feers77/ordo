"""HITL por Telegram (F1-07) — tests escritos antes de la implementación.

Telegram es un canal público: cualquiera puede escribirle al bot. Todo lo que
importa aquí es que sólo el aprobador legítimo, desde un chat verificado por él
mismo, pueda resolver una solicitud.
"""

import uuid
from typing import Any

import httpx
import pytest
from ordo_iam.notifications import NOTIFY_APPROVAL_JOB, InMemorySender, run_pending_notifications
from ordo_iam.telegram import WEBHOOK_SECRET_HEADER, sign_callback, verify_callback
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

pytestmark = pytest.mark.integration

SECRET = "secreto-de-webhook-de-prueba"

OPERATION = {
    "model": "account.move",
    "operation": "action_post",
    "amount": {"currency": "CLP", "value": "1500000"},
    "payload": {"move_id": 42},
}
CAP = {
    "models": {"account.move": ["read", "write"]},
    "requires_approval": ["account.move.action_post"],
}


@pytest.fixture(autouse=True)
def telegram_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TELEGRAM_WEBHOOK_SECRET", SECRET)
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "1234:token-de-prueba")


@pytest.fixture(autouse=True)
def no_network(monkeypatch: pytest.MonkeyPatch) -> None:
    """Ningún test puede tocar la API de Telegram: si alguien lo intenta, falla."""
    from ordo_iam.telegram import TelegramSender

    async def explode(*_args: Any, **_kwargs: Any) -> None:
        raise AssertionError("el canal real no debe usarse en tests")

    monkeypatch.setattr(TelegramSender, "send", explode)


# --------------------------------------------------------------------- helpers


def message_update(chat_id: int, body: str) -> dict[str, Any]:
    return {
        "update_id": 1,
        "message": {
            "message_id": 7,
            "date": 1750000000,
            "from": {"id": chat_id, "is_bot": False, "first_name": "Ana"},
            "chat": {"id": chat_id, "type": "private"},
            "text": body,
        },
    }


def callback_update(chat_id: int, data: str) -> dict[str, Any]:
    return {
        "update_id": 2,
        "callback_query": {
            "id": "cb-1",
            "from": {"id": chat_id, "is_bot": False, "first_name": "Ana"},
            "message": {"message_id": 8, "chat": {"id": chat_id, "type": "private"}},
            "data": data,
        },
    }


async def webhook(
    client: httpx.AsyncClient, update: dict[str, Any], *, secret: str | None = SECRET
) -> httpx.Response:
    headers = {} if secret is None else {WEBHOOK_SECRET_HEADER: secret}
    return await client.post("/iam/v1/telegram/webhook", json=update, headers=headers)


async def agent_with_token(
    client: httpx.AsyncClient,
    session: AsyncSession,
    helpers: Any,
    email: str,
) -> tuple[str, str, str]:
    """Devuelve (owner_token, agent_id, agent_token)."""
    owner_token, agent_id, secret = await helpers.setup_agent(client, session, email=email, cap=CAP)
    exchanged = await helpers.do_exchange(
        client, subject_token=owner_token, agent_id=agent_id, secret=secret
    )
    return owner_token, agent_id, exchanged.json()["access_token"]


async def issue_link_code(client: httpx.AsyncClient, user_token: str) -> str:
    resp = await client.post(
        "/iam/v1/channels/telegram/link",
        headers={"Authorization": f"Bearer {user_token}"},
    )
    assert resp.status_code == 201, resp.text
    return str(resp.json()["code"])


async def link_chat(client: httpx.AsyncClient, user_token: str, chat_id: int) -> None:
    code = await issue_link_code(client, user_token)
    resp = await webhook(client, message_update(chat_id, f"/start {code}"))
    assert resp.status_code == 200, resp.text
    assert resp.json()["action"] == "linked"


async def create_approval(client: httpx.AsyncClient, agent_token: str) -> str:
    resp = await client.post(
        "/iam/v1/approvals",
        json={"operation": OPERATION},
        headers={"Authorization": f"Bearer {agent_token}", "Idempotency-Key": uuid.uuid4().hex},
    )
    assert resp.status_code == 201, resp.text
    return str(resp.json()["approval_id"])


async def approval_row(session: AsyncSession, approval_id: str) -> Any:
    row = (
        await session.execute(
            text(
                "SELECT status, approver_id, resolved_at FROM iam_approval_request WHERE id = :id"
            ),
            {"id": approval_id},
        )
    ).first()
    assert row is not None
    return row


# ----------------------------------------------------------------------- tests


class TestChannelVerification:
    async def test_link_code_is_single_use(
        self,
        api_client: httpx.AsyncClient,
        session: AsyncSession,
        helpers: Any,
    ) -> None:
        owner_token, _, _ = await agent_with_token(api_client, session, helpers, "tg1@acme.cl")
        code = await issue_link_code(api_client, owner_token)

        first = await webhook(api_client, message_update(9001, code))
        assert first.status_code == 200, first.text
        assert first.json()["action"] == "linked"

        second = await webhook(api_client, message_update(9002, code))
        assert second.status_code == 400
        assert second.json()["error"]["code"] == "IAM_LINK_CODE_INVALID"

    async def test_link_code_expires(
        self,
        api_client: httpx.AsyncClient,
        session: AsyncSession,
        helpers: Any,
    ) -> None:
        owner_token, _, _ = await agent_with_token(api_client, session, helpers, "tg2@acme.cl")
        code = await issue_link_code(api_client, owner_token)
        await session.execute(
            text("UPDATE iam_channel_link_code SET expires_at = now() - interval '1 minute'")
        )
        await session.commit()

        resp = await webhook(api_client, message_update(9003, code))
        assert resp.status_code == 400
        assert resp.json()["error"]["code"] == "IAM_LINK_CODE_INVALID"

    async def test_unknown_code_never_links(
        self,
        api_client: httpx.AsyncClient,
        session: AsyncSession,
        helpers: Any,
    ) -> None:
        await agent_with_token(api_client, session, helpers, "tg3@acme.cl")
        resp = await webhook(api_client, message_update(9004, "ABCDEFGHJK"))
        assert resp.status_code == 400
        assert resp.json()["error"]["code"] == "IAM_LINK_CODE_INVALID"

    async def test_link_requires_authentication(self, api_client: httpx.AsyncClient) -> None:
        resp = await api_client.post("/iam/v1/channels/telegram/link")
        assert resp.status_code == 401
        assert resp.json()["error"]["code"] == "IAM_TOKEN_INVALID"

    async def test_chat_already_linked_to_another_principal_rejected(
        self,
        api_client: httpx.AsyncClient,
        session: AsyncSession,
        helpers: Any,
    ) -> None:
        from ordo_iam.repository import PrincipalRepository

        owner_token, _, _ = await agent_with_token(api_client, session, helpers, "tg4@acme.cl")
        await link_chat(api_client, owner_token, 9005)

        await PrincipalRepository(session).create_user(
            tenant="acme", email="otro4@acme.cl", display_name="Otro"
        )
        other_token = helpers.kc_token("kc-otro4@acme.cl", "otro4@acme.cl")
        code = await issue_link_code(api_client, other_token)
        resp = await webhook(api_client, message_update(9005, code))
        assert resp.status_code == 409
        assert resp.json()["error"]["code"] == "IAM_CHANNEL_ALREADY_LINKED"


class TestWebhookAuthentication:
    async def test_missing_secret_header_is_refused_and_nothing_is_processed(
        self,
        api_client: httpx.AsyncClient,
        session: AsyncSession,
        helpers: Any,
    ) -> None:
        owner_token, _, agent_token = await agent_with_token(
            api_client, session, helpers, "tg5@acme.cl"
        )
        await link_chat(api_client, owner_token, 9006)
        approval_id = await create_approval(api_client, agent_token)

        data = sign_callback(uuid.UUID(approval_id), approve=True)
        resp = await webhook(api_client, callback_update(9006, data), secret=None)
        assert resp.status_code in (401, 403)
        assert resp.json()["error"]["code"] == "IAM_WEBHOOK_UNAUTHORIZED"
        assert (await approval_row(session, approval_id)).status == "pending"

    async def test_wrong_secret_header_is_refused(
        self,
        api_client: httpx.AsyncClient,
        session: AsyncSession,
        helpers: Any,
    ) -> None:
        owner_token, _, agent_token = await agent_with_token(
            api_client, session, helpers, "tg6@acme.cl"
        )
        await link_chat(api_client, owner_token, 9007)
        approval_id = await create_approval(api_client, agent_token)

        data = sign_callback(uuid.UUID(approval_id), approve=True)
        resp = await webhook(api_client, callback_update(9007, data), secret="no-es-el-secreto")
        assert resp.status_code in (401, 403)
        assert resp.json()["error"]["code"] == "IAM_WEBHOOK_UNAUTHORIZED"
        assert (await approval_row(session, approval_id)).status == "pending"


class TestCallbackAuthorization:
    async def test_unverified_chat_cannot_resolve(
        self,
        api_client: httpx.AsyncClient,
        session: AsyncSession,
        helpers: Any,
    ) -> None:
        owner_token, _, agent_token = await agent_with_token(
            api_client, session, helpers, "tg7@acme.cl"
        )
        await issue_link_code(api_client, owner_token)  # emitido pero jamás canjeado
        approval_id = await create_approval(api_client, agent_token)

        data = sign_callback(uuid.UUID(approval_id), approve=True)
        resp = await webhook(api_client, callback_update(9008, data))
        assert resp.status_code == 403
        assert resp.json()["error"]["code"] == "IAM_CHANNEL_NOT_VERIFIED"
        assert (await approval_row(session, approval_id)).status == "pending"

    async def test_forged_callback_signature_rejected(
        self,
        api_client: httpx.AsyncClient,
        session: AsyncSession,
        helpers: Any,
    ) -> None:
        owner_token, _, agent_token = await agent_with_token(
            api_client, session, helpers, "tg8@acme.cl"
        )
        await link_chat(api_client, owner_token, 9009)
        approval_id = await create_approval(api_client, agent_token)

        forged = f"a1:{uuid.UUID(approval_id).hex}:a:{'0' * 20}"
        resp = await webhook(api_client, callback_update(9009, forged))
        assert resp.status_code == 403
        assert resp.json()["error"]["code"] == "IAM_CALLBACK_INVALID"
        assert (await approval_row(session, approval_id)).status == "pending"

    async def test_verified_chat_of_another_user_cannot_resolve(
        self,
        api_client: httpx.AsyncClient,
        session: AsyncSession,
        helpers: Any,
    ) -> None:
        from ordo_iam.repository import PrincipalRepository

        owner_token, _, agent_token = await agent_with_token(
            api_client, session, helpers, "tg9@acme.cl"
        )
        await link_chat(api_client, owner_token, 9010)
        approval_id = await create_approval(api_client, agent_token)

        await PrincipalRepository(session).create_user(
            tenant="acme", email="ajeno9@acme.cl", display_name="Ajeno"
        )
        stranger_token = helpers.kc_token("kc-ajeno9@acme.cl", "ajeno9@acme.cl")
        await link_chat(api_client, stranger_token, 9011)

        data = sign_callback(uuid.UUID(approval_id), approve=True)
        resp = await webhook(api_client, callback_update(9011, data))
        assert resp.status_code == 403
        assert resp.json()["error"]["code"] == "IAM_NOT_APPROVER"
        assert (await approval_row(session, approval_id)).status == "pending"

    async def test_callback_for_unknown_approval_is_not_found(
        self,
        api_client: httpx.AsyncClient,
        session: AsyncSession,
        helpers: Any,
    ) -> None:
        owner_token, _, _ = await agent_with_token(api_client, session, helpers, "tg10@acme.cl")
        await link_chat(api_client, owner_token, 9012)
        data = sign_callback(uuid.uuid4(), approve=True)
        resp = await webhook(api_client, callback_update(9012, data))
        assert resp.status_code == 404
        assert resp.json()["error"]["code"] == "IAM_APPROVAL_NOT_FOUND"


class TestResolutionParity:
    async def test_approving_by_telegram_leaves_the_same_state_as_the_api(
        self,
        api_client: httpx.AsyncClient,
        session: AsyncSession,
        helpers: Any,
    ) -> None:
        owner_token, _, agent_token = await agent_with_token(
            api_client, session, helpers, "tg11@acme.cl"
        )
        await link_chat(api_client, owner_token, 9013)

        by_api = await create_approval(api_client, agent_token)
        by_telegram = await create_approval(api_client, agent_token)

        api_resp = await api_client.post(
            f"/iam/v1/approvals/{by_api}/approve",
            headers={"Authorization": f"Bearer {owner_token}"},
        )
        assert api_resp.status_code == 200, api_resp.text

        data = sign_callback(uuid.UUID(by_telegram), approve=True)
        tg_resp = await webhook(api_client, callback_update(9013, data))
        assert tg_resp.status_code == 200, tg_resp.text
        assert tg_resp.json()["action"] == "approved"

        api_row = await approval_row(session, by_api)
        tg_row = await approval_row(session, by_telegram)
        assert tg_row.status == api_row.status == "approved"
        assert tg_row.approver_id == api_row.approver_id
        assert tg_row.resolved_at is not None

        consume = await api_client.post(
            f"/iam/v1/approvals/{by_telegram}/consume",
            json={"operation": OPERATION},
            headers={"Authorization": f"Bearer {agent_token}"},
        )
        assert consume.status_code == 200, consume.text
        assert consume.json()["status"] == "consumed"

    async def test_rejecting_by_telegram_blocks_consumption(
        self,
        api_client: httpx.AsyncClient,
        session: AsyncSession,
        helpers: Any,
    ) -> None:
        owner_token, _, agent_token = await agent_with_token(
            api_client, session, helpers, "tg12@acme.cl"
        )
        await link_chat(api_client, owner_token, 9014)
        approval_id = await create_approval(api_client, agent_token)

        data = sign_callback(uuid.UUID(approval_id), approve=False)
        resp = await webhook(api_client, callback_update(9014, data))
        assert resp.status_code == 200, resp.text
        assert resp.json()["action"] == "rejected"

        consume = await api_client.post(
            f"/iam/v1/approvals/{approval_id}/consume",
            json={"operation": OPERATION},
            headers={"Authorization": f"Bearer {agent_token}"},
        )
        assert consume.status_code == 403
        assert consume.json()["error"]["code"] == "IAM_APPROVAL_REJECTED"

    async def test_a_resolved_request_cannot_be_resolved_again_by_telegram(
        self,
        api_client: httpx.AsyncClient,
        session: AsyncSession,
        helpers: Any,
    ) -> None:
        owner_token, _, agent_token = await agent_with_token(
            api_client, session, helpers, "tg13@acme.cl"
        )
        await link_chat(api_client, owner_token, 9015)
        approval_id = await create_approval(api_client, agent_token)

        data = sign_callback(uuid.UUID(approval_id), approve=True)
        assert (await webhook(api_client, callback_update(9015, data))).status_code == 200
        second = await webhook(api_client, callback_update(9015, data))
        assert second.status_code == 409
        assert second.json()["error"]["code"] == "IAM_APPROVAL_CONSUMED"


class TestNotificationIsQueued:
    async def test_creating_an_approval_enqueues_the_job_and_sends_nothing_in_request(
        self,
        api_client: httpx.AsyncClient,
        session: AsyncSession,
        helpers: Any,
    ) -> None:
        owner_token, _, agent_token = await agent_with_token(
            api_client, session, helpers, "tg14@acme.cl"
        )
        await link_chat(api_client, owner_token, 9016)
        await session.execute(text("DELETE FROM ir_job WHERE state = 'pending'"))
        await session.commit()

        sender = InMemorySender()
        approval_id = await create_approval(api_client, agent_token)
        assert sender.sent == []  # nada sale a la red dentro del request

        jobs = (
            await session.execute(
                text("SELECT name, payload FROM ir_job WHERE state = 'pending' ORDER BY id")
            )
        ).all()
        assert [job.name for job in jobs] == [NOTIFY_APPROVAL_JOB]
        assert jobs[0].payload["approval_id"] == approval_id

        processed = await run_pending_notifications(session, sender)
        assert processed == 1
        assert len(sender.sent) == 1
        message = sender.sent[0]
        assert message.address == "9016"
        assert "account.move" in message.text
        assert "1500000" in message.text
        assert [b.callback_data for b in message.buttons] == [
            sign_callback(uuid.UUID(approval_id), approve=True),
            sign_callback(uuid.UUID(approval_id), approve=False),
        ]
        assert verify_callback(message.buttons[0].callback_data) == (
            uuid.UUID(approval_id),
            True,
        )
        remaining = (
            await session.execute(text("SELECT count(*) FROM ir_job WHERE state = 'pending'"))
        ).scalar()
        assert remaining == 0

    async def test_approver_without_verified_channel_does_not_block_the_queue(
        self,
        api_client: httpx.AsyncClient,
        session: AsyncSession,
        helpers: Any,
    ) -> None:
        _, _, agent_token = await agent_with_token(api_client, session, helpers, "tg15@acme.cl")
        await session.execute(text("DELETE FROM ir_job WHERE state = 'pending'"))
        await session.commit()

        await create_approval(api_client, agent_token)
        sender = InMemorySender()
        assert await run_pending_notifications(session, sender) == 1
        assert sender.sent == []
        done = (
            await session.execute(text("SELECT count(*) FROM ir_job WHERE state = 'done'"))
        ).scalar()
        assert done is not None and done >= 1

    async def test_resolving_by_telegram_confirms_by_a_queued_message(
        self,
        api_client: httpx.AsyncClient,
        session: AsyncSession,
        helpers: Any,
    ) -> None:
        owner_token, _, agent_token = await agent_with_token(
            api_client, session, helpers, "tg16@acme.cl"
        )
        await link_chat(api_client, owner_token, 9017)
        approval_id = await create_approval(api_client, agent_token)
        await session.execute(text("DELETE FROM ir_job WHERE state = 'pending'"))
        await session.commit()

        data = sign_callback(uuid.UUID(approval_id), approve=True)
        assert (await webhook(api_client, callback_update(9017, data))).status_code == 200

        sender = InMemorySender()
        assert await run_pending_notifications(session, sender) == 1
        assert len(sender.sent) == 1
        assert sender.sent[0].address == "9017"
        assert sender.sent[0].buttons == ()
