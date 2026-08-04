"""Tests del flujo HITL de aprobaciones (F1-06) — escritos antes de implementar."""

import uuid
from typing import Any

import httpx
import pytest
from ordo_iam.audit import verify_chain
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

pytestmark = pytest.mark.integration

OPERATION = {
    "model": "account.move",
    "operation": "action_post",
    "amount": {"currency": "CLP", "value": "1500000"},
    "payload": {"move_id": 42},
}


async def agent_with_token(client: httpx.AsyncClient, session: AsyncSession, helpers, email: str):  # type: ignore[no-untyped-def]
    cap = {
        "models": {"account.move": ["read", "write"]},
        "requires_approval": ["account.move.action_post"],
    }
    owner_token, agent_id, secret = await helpers.setup_agent(client, session, email=email, cap=cap)
    exchanged = await helpers.do_exchange(
        client, subject_token=owner_token, agent_id=agent_id, secret=secret
    )
    return owner_token, agent_id, exchanged.json()["access_token"]


async def create_approval(
    client: httpx.AsyncClient,
    agent_token: str,
    idem_key: str,
    operation: dict[str, Any] | None = None,
) -> httpx.Response:
    return await client.post(
        "/iam/v1/approvals",
        json={"operation": operation or OPERATION},
        headers={
            "Authorization": f"Bearer {agent_token}",
            "Idempotency-Key": idem_key,
        },
    )


class TestApprovalLifecycle:
    async def test_create_returns_pending_with_expiry(
        self,
        api_client: httpx.AsyncClient,
        session: AsyncSession,
        helpers,  # type: ignore[no-untyped-def]
    ) -> None:
        _, _, agent_token = await agent_with_token(api_client, session, helpers, "h1@acme.cl")
        resp = await create_approval(api_client, agent_token, uuid.uuid4().hex)
        assert resp.status_code == 201, resp.text
        body = resp.json()
        assert body["status"] == "pending"
        assert body["expires_at"]

    async def test_same_idempotency_key_same_request(
        self,
        api_client: httpx.AsyncClient,
        session: AsyncSession,
        helpers,  # type: ignore[no-untyped-def]
    ) -> None:
        _, _, agent_token = await agent_with_token(api_client, session, helpers, "h2@acme.cl")
        key = uuid.uuid4().hex
        first = await create_approval(api_client, agent_token, key)
        second = await create_approval(api_client, agent_token, key)
        assert second.status_code == 200
        assert second.json()["approval_id"] == first.json()["approval_id"]

    async def test_owner_approves_then_agent_consumes_once(
        self,
        api_client: httpx.AsyncClient,
        session: AsyncSession,
        helpers,  # type: ignore[no-untyped-def]
    ) -> None:
        owner_token, _, agent_token = await agent_with_token(
            api_client, session, helpers, "h3@acme.cl"
        )
        created = await create_approval(api_client, agent_token, uuid.uuid4().hex)
        approval_id = created.json()["approval_id"]

        ok = await api_client.post(
            f"/iam/v1/approvals/{approval_id}/approve",
            headers={"Authorization": f"Bearer {owner_token}"},
        )
        assert ok.status_code == 200, ok.text
        assert ok.json()["status"] == "approved"

        status = await api_client.get(
            f"/iam/v1/approvals/{approval_id}",
            headers={"Authorization": f"Bearer {agent_token}"},
        )
        assert status.json()["status"] == "approved"

        consume = await api_client.post(
            f"/iam/v1/approvals/{approval_id}/consume",
            json={"operation": OPERATION},
            headers={"Authorization": f"Bearer {agent_token}"},
        )
        assert consume.status_code == 200, consume.text
        assert consume.json()["status"] == "consumed"

        again = await api_client.post(
            f"/iam/v1/approvals/{approval_id}/consume",
            json={"operation": OPERATION},
            headers={"Authorization": f"Bearer {agent_token}"},
        )
        assert again.status_code == 409
        assert again.json()["error"]["code"] == "IAM_APPROVAL_CONSUMED"

    async def test_consume_without_approval_pending(
        self,
        api_client: httpx.AsyncClient,
        session: AsyncSession,
        helpers,  # type: ignore[no-untyped-def]
    ) -> None:
        _, _, agent_token = await agent_with_token(api_client, session, helpers, "h4@acme.cl")
        created = await create_approval(api_client, agent_token, uuid.uuid4().hex)
        approval_id = created.json()["approval_id"]
        resp = await api_client.post(
            f"/iam/v1/approvals/{approval_id}/consume",
            json={"operation": OPERATION},
            headers={"Authorization": f"Bearer {agent_token}"},
        )
        assert resp.status_code == 409
        body = resp.json()["error"]
        assert body["code"] == "IAM_APPROVAL_PENDING"
        assert body["retryable"] is True

    async def test_rejected_cannot_consume(
        self,
        api_client: httpx.AsyncClient,
        session: AsyncSession,
        helpers,  # type: ignore[no-untyped-def]
    ) -> None:
        owner_token, _, agent_token = await agent_with_token(
            api_client, session, helpers, "h5@acme.cl"
        )
        created = await create_approval(api_client, agent_token, uuid.uuid4().hex)
        approval_id = created.json()["approval_id"]
        await api_client.post(
            f"/iam/v1/approvals/{approval_id}/reject",
            headers={"Authorization": f"Bearer {owner_token}"},
        )
        resp = await api_client.post(
            f"/iam/v1/approvals/{approval_id}/consume",
            json={"operation": OPERATION},
            headers={"Authorization": f"Bearer {agent_token}"},
        )
        assert resp.status_code == 403
        assert resp.json()["error"]["code"] == "IAM_APPROVAL_REJECTED"

    async def test_expired_cannot_consume(
        self,
        api_client: httpx.AsyncClient,
        session: AsyncSession,
        helpers,  # type: ignore[no-untyped-def]
    ) -> None:
        owner_token, _, agent_token = await agent_with_token(
            api_client, session, helpers, "h6@acme.cl"
        )
        created = await create_approval(api_client, agent_token, uuid.uuid4().hex)
        approval_id = created.json()["approval_id"]
        await session.execute(
            text(
                "UPDATE iam_approval_request SET expires_at = now() - interval '1 minute' "
                "WHERE id = :id"
            ),
            {"id": approval_id},
        )
        await session.commit()
        await api_client.post(
            f"/iam/v1/approvals/{approval_id}/approve",
            headers={"Authorization": f"Bearer {owner_token}"},
        )
        resp = await api_client.post(
            f"/iam/v1/approvals/{approval_id}/consume",
            json={"operation": OPERATION},
            headers={"Authorization": f"Bearer {agent_token}"},
        )
        assert resp.status_code == 410
        assert resp.json()["error"]["code"] == "IAM_APPROVAL_EXPIRED"

    async def test_tampered_operation_rejected(
        self,
        api_client: httpx.AsyncClient,
        session: AsyncSession,
        helpers,  # type: ignore[no-untyped-def]
    ) -> None:
        owner_token, _, agent_token = await agent_with_token(
            api_client, session, helpers, "h7@acme.cl"
        )
        created = await create_approval(api_client, agent_token, uuid.uuid4().hex)
        approval_id = created.json()["approval_id"]
        await api_client.post(
            f"/iam/v1/approvals/{approval_id}/approve",
            headers={"Authorization": f"Bearer {owner_token}"},
        )
        evil = dict(OPERATION, amount={"currency": "CLP", "value": "999999999"})
        resp = await api_client.post(
            f"/iam/v1/approvals/{approval_id}/consume",
            json={"operation": evil},
            headers={"Authorization": f"Bearer {agent_token}"},
        )
        assert resp.status_code == 409
        assert resp.json()["error"]["code"] == "IAM_APPROVAL_MISMATCH"

    async def test_only_owner_can_approve(
        self,
        api_client: httpx.AsyncClient,
        session: AsyncSession,
        helpers,  # type: ignore[no-untyped-def]
    ) -> None:
        from ordo_iam.repository import PrincipalRepository

        _, _, agent_token = await agent_with_token(api_client, session, helpers, "h8@acme.cl")
        created = await create_approval(api_client, agent_token, uuid.uuid4().hex)
        approval_id = created.json()["approval_id"]
        repo = PrincipalRepository(session)
        await repo.create_user(tenant="acme", email="ajena@acme.cl", display_name="X")
        stranger_token = helpers.kc_token("kc-ajena@acme.cl", "ajena@acme.cl")
        resp = await api_client.post(
            f"/iam/v1/approvals/{approval_id}/approve",
            headers={"Authorization": f"Bearer {stranger_token}"},
        )
        assert resp.status_code == 403
        assert resp.json()["error"]["code"] == "IAM_NOT_APPROVER"

    async def test_transitions_are_audited(
        self,
        api_client: httpx.AsyncClient,
        session: AsyncSession,
        helpers,  # type: ignore[no-untyped-def]
    ) -> None:
        owner_token, _, agent_token = await agent_with_token(
            api_client, session, helpers, "h9@acme.cl"
        )
        created = await create_approval(api_client, agent_token, uuid.uuid4().hex)
        approval_id = created.json()["approval_id"]
        await api_client.post(
            f"/iam/v1/approvals/{approval_id}/approve",
            headers={"Authorization": f"Bearer {owner_token}"},
        )
        ok, broken = await verify_chain(session, "acme")
        assert ok, f"cadena rota en {broken}"
        rows = (
            await session.execute(
                text(
                    "SELECT count(*) FROM iam_audit_log WHERE tenant='acme' "
                    "AND event_type LIKE 'approval_%' "
                    "AND payload->>'approval_id' = :aid"
                ),
                {"aid": str(approval_id)},
            )
        ).scalar()
        assert rows is not None and rows >= 2  # created + approved
