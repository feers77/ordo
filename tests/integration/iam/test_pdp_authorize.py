"""Integración PDP + /iam/v1/authorize + auditoría encadenada (F1-05)."""

import uuid
from collections.abc import AsyncIterator

import httpx
import pytest
from ordo_iam.api import get_usage_counter
from ordo_iam.audit import append_audit, verify_chain
from ordo_iam.models import Acl, RecordRule, Role, RoleMember
from ordo_iam.pdp import AccessRequest, InMemoryUsageCounter, PolicyEngine
from ordo_iam.repository import PrincipalRepository
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

pytestmark = pytest.mark.integration


async def seed_rbac(
    session: AsyncSession,
    user_id: uuid.UUID,
    *,
    model: str = "sale.order",
    tenant: str = "acme",
    read: bool = True,
    write: bool = True,
    create: bool = True,
    unlink: bool = False,
) -> Role:
    role = Role(tenant=tenant, name=f"rol-{uuid.uuid4().hex[:6]}")
    session.add(role)
    await session.flush()
    session.add(RoleMember(role_id=role.id, principal_id=user_id))
    session.add(
        Acl(
            role_id=role.id,
            model=model,
            perm_read=read,
            perm_write=write,
            perm_create=create,
            perm_unlink=unlink,
        )
    )
    await session.commit()
    return role


class TestPolicyEngineRBAC:
    async def test_user_with_acl_allowed(self, session: AsyncSession) -> None:
        repo = PrincipalRepository(session)
        user = await repo.create_user(tenant="acme", email="p1@acme.cl", display_name="P")
        await seed_rbac(session, user.principal_id)
        engine = PolicyEngine(session, InMemoryUsageCounter())
        decision = await engine.evaluate(
            AccessRequest(
                tenant="acme", model="sale.order", operation="read", user_id=user.principal_id
            ),
            cap=None,
        )
        assert decision.allowed

    async def test_user_without_acl_denied(self, session: AsyncSession) -> None:
        repo = PrincipalRepository(session)
        user = await repo.create_user(tenant="acme", email="p2@acme.cl", display_name="P")
        engine = PolicyEngine(session, InMemoryUsageCounter())
        decision = await engine.evaluate(
            AccessRequest(
                tenant="acme", model="sale.order", operation="read", user_id=user.principal_id
            ),
            cap=None,
        )
        assert not decision.allowed
        assert decision.reason == "RBAC_DENIED"

    async def test_agent_cap_cannot_exceed_user_rbac(self, session: AsyncSession) -> None:
        """Intersección: cap permite pero el usuario no tiene ACL ⇒ deniega."""
        repo = PrincipalRepository(session)
        user = await repo.create_user(tenant="acme", email="p3@acme.cl", display_name="P")
        engine = PolicyEngine(session, InMemoryUsageCounter())
        decision = await engine.evaluate(
            AccessRequest(
                tenant="acme",
                model="sale.order",
                operation="read",
                agent_id="a-1",
                user_id=user.principal_id,
            ),
            cap={"models": {"sale.order": ["read"]}},
        )
        assert not decision.allowed
        assert decision.reason == "RBAC_DENIED"

    async def test_record_rules_global_and_role_or(self, session: AsyncSession) -> None:
        repo = PrincipalRepository(session)
        user = await repo.create_user(tenant="acme", email="p4@acme.cl", display_name="P")
        role = await seed_rbac(session, user.principal_id)
        other_role = Role(tenant="acme", name=f"ajeno-{uuid.uuid4().hex[:6]}")
        session.add(other_role)
        await session.flush()
        session.add_all(
            [
                RecordRule(
                    tenant="acme",
                    model="sale.order",
                    name="global",
                    domain=[["company_id", "=", 1]],
                    ops=["read"],
                ),
                RecordRule(
                    tenant="acme",
                    model="sale.order",
                    name="mi rol",
                    domain=[["team", "=", "ventas"]],
                    ops=["read"],
                    role_id=role.id,
                ),
                RecordRule(
                    tenant="acme",
                    model="sale.order",
                    name="rol ajeno",
                    domain=[["team", "=", "otro"]],
                    ops=["read"],
                    role_id=other_role.id,
                ),
            ]
        )
        await session.commit()
        engine = PolicyEngine(session, InMemoryUsageCounter())
        decision = await engine.evaluate(
            AccessRequest(
                tenant="acme", model="sale.order", operation="read", user_id=user.principal_id
            ),
            cap=None,
        )
        assert decision.allowed
        assert decision.record_domain["global_and"] == [[["company_id", "=", 1]]]
        assert decision.record_domain["role_or"] == [[["team", "=", "ventas"]]]


class TestAuthorizeEndpoint:
    @pytest.fixture
    async def client(self, api_client: httpx.AsyncClient) -> AsyncIterator[httpx.AsyncClient]:
        from ordo_iam.main import app

        app.dependency_overrides[get_usage_counter] = InMemoryUsageCounter
        yield api_client
        app.dependency_overrides.pop(get_usage_counter, None)

    async def test_agent_token_authorize_allow_and_deny(
        self,
        client: httpx.AsyncClient,
        session: AsyncSession,
        helpers,  # type: ignore[no-untyped-def]
    ) -> None:
        owner_token, agent_id, secret = await helpers.setup_agent(
            client, session, email="a1@acme.cl"
        )
        me = await client.get("/iam/v1/me", headers={"Authorization": f"Bearer {owner_token}"})
        await seed_rbac(session, uuid.UUID(me.json()["principal_id"]))
        exchanged = await helpers.do_exchange(
            client, subject_token=owner_token, agent_id=agent_id, secret=secret
        )
        agent_token = exchanged.json()["access_token"]

        allowed = await client.post(
            "/iam/v1/authorize",
            json={"model": "sale.order", "operation": "read"},
            headers={"Authorization": f"Bearer {agent_token}"},
        )
        assert allowed.status_code == 200, allowed.text
        assert allowed.json()["allowed"] is True

        denied = await client.post(
            "/iam/v1/authorize",
            json={"model": "stock.picking", "operation": "read"},
            headers={"Authorization": f"Bearer {agent_token}"},
        )
        assert denied.json()["allowed"] is False
        assert denied.json()["reason"] == "CAP_NOT_GRANTED"

    async def test_user_token_authorize_rbac_only(
        self,
        client: httpx.AsyncClient,
        session: AsyncSession,
        helpers,  # type: ignore[no-untyped-def]
    ) -> None:
        repo = PrincipalRepository(session)
        user = await repo.create_user(tenant="acme", email="a2@acme.cl", display_name="A")
        await seed_rbac(session, user.principal_id, model="res.partner")
        user_token = helpers.kc_token("kc-a2@acme.cl", "a2@acme.cl")
        resp = await client.post(
            "/iam/v1/authorize",
            json={"model": "res.partner", "operation": "read"},
            headers={"Authorization": f"Bearer {user_token}"},
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["allowed"] is True

    async def test_authorize_decisions_are_audited(
        self,
        client: httpx.AsyncClient,
        session: AsyncSession,
        helpers,  # type: ignore[no-untyped-def]
    ) -> None:
        repo = PrincipalRepository(session)
        await repo.create_user(tenant="audited", email="a3@aud.cl", display_name="A")
        user_token = helpers.kc_token("kc-a3@aud.cl", "a3@aud.cl", tenant="audited")
        await client.post(
            "/iam/v1/authorize",
            json={"model": "res.partner", "operation": "read"},
            headers={"Authorization": f"Bearer {user_token}"},
        )
        ok, broken = await verify_chain(session, "audited")
        assert ok, f"cadena rota en id {broken}"


class TestAuditChain:
    async def test_chain_links_and_verifies(self, session: AsyncSession) -> None:
        for i in range(3):
            await append_audit(session, tenant="chain1", event_type="test", payload={"i": i})
        ok, broken = await verify_chain(session, "chain1")
        assert ok and broken is None

    async def test_tampering_detected(self, session: AsyncSession) -> None:
        rows = [
            await append_audit(session, tenant="chain2", event_type="test", payload={"i": i})
            for i in range(3)
        ]
        await session.execute(
            text("UPDATE iam_audit_log SET payload = '{\"i\": 999}' WHERE id = :id"),
            {"id": rows[1].id},
        )
        await session.commit()
        ok, broken = await verify_chain(session, "chain2")
        assert not ok
        assert broken == rows[1].id

    async def test_deleting_row_detected(self, session: AsyncSession) -> None:
        rows = [
            await append_audit(session, tenant="chain3", event_type="test", payload={"i": i})
            for i in range(3)
        ]
        await session.execute(text("DELETE FROM iam_audit_log WHERE id = :id"), {"id": rows[1].id})
        await session.commit()
        ok, broken = await verify_chain(session, "chain3")
        assert not ok
        assert broken == rows[2].id
