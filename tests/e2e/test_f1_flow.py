"""E2E de la Fase 1: Keycloak real + ordo-iam real, flujo completo.

login OIDC → vinculación de identidad → registro de agente → grant →
token exchange (act) → authorize (allow/deny/monto) → HITL → auditoría.
"""

import asyncio
import os
import uuid
from typing import Any

import httpx
import pytest

pytestmark = pytest.mark.e2e

KEYCLOAK_URL = os.environ.get("E2E_KEYCLOAK_URL", "http://127.0.0.1:8080")
REALM = "ordo"

CAP = {
    "models": {"sale.order": ["read", "create"], "account.move": ["read", "write"]},
    "limits": {"max_amount_per_op": {"CLP": 5_000_000}},
    "requires_approval": ["account.move.action_post"],
    "deny": ["res.users.*"],
}


def user_access_token(email: str, password: str) -> str:
    resp = httpx.post(
        f"{KEYCLOAK_URL}/realms/{REALM}/protocol/openid-connect/token",
        data={
            "grant_type": "password",
            "client_id": "ordo-cli",
            "username": email,
            "password": password,
            "scope": "openid",
        },
        timeout=10,
    )
    assert resp.status_code == 200, resp.text
    return str(resp.json()["access_token"])


async def provision_user(db_url: str, email: str, tenant: str) -> None:
    """Un admin del tenant crea el usuario (ordo nunca auto-crea identidades)."""
    from ordo_iam.repository import PrincipalRepository
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    engine = create_async_engine(db_url)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    async with maker() as session:
        await PrincipalRepository(session).create_user(
            tenant=tenant, email=email, display_name="E2E"
        )
    await engine.dispose()


async def seed_acls(db_url: str, principal_id: str, tenant: str) -> None:
    import uuid as _uuid

    from ordo_iam.models import Acl, Role, RoleMember
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    engine = create_async_engine(db_url)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    async with maker() as session:
        role = Role(tenant=tenant, name=f"e2e-{_uuid.uuid4().hex[:6]}")
        session.add(role)
        await session.flush()
        session.add(RoleMember(role_id=role.id, principal_id=_uuid.UUID(principal_id)))
        for model in ("sale.order", "account.move"):
            session.add(
                Acl(
                    role_id=role.id,
                    model=model,
                    perm_read=True,
                    perm_write=True,
                    perm_create=True,
                )
            )
        await session.commit()
    await engine.dispose()


class TestFase1EndToEnd:
    async def test_full_delegation_flow(
        self,
        iam_service: str,
        kc_user: tuple[str, str, str],
        e2e_db_url: str,
    ) -> None:
        email, password, tenant = kc_user
        await provision_user(e2e_db_url, email, tenant)

        async with httpx.AsyncClient(base_url=iam_service, timeout=20) as client:
            # 1. login real contra Keycloak y vinculación de identidad
            user_token = await asyncio.to_thread(user_access_token, email, password)
            me = await client.get("/iam/v1/me", headers={"Authorization": f"Bearer {user_token}"})
            assert me.status_code == 200, me.text
            principal_id = me.json()["principal_id"]
            assert me.json()["tenant"] == tenant
            await seed_acls(e2e_db_url, principal_id, tenant)

            auth_user = {"Authorization": f"Bearer {user_token}"}

            # 2. registro de agente
            reg = await client.post(
                "/iam/v1/agents",
                json={"display_name": "agente e2e", "model": "agente-v1"},
                headers=auth_user,
            )
            assert reg.status_code == 201, reg.text
            agent_id = reg.json()["agent_id"]
            agent_secret = reg.json()["agent_secret"]

            # 3. sin grants el exchange debe fallar
            def exchange_data() -> dict[str, str]:
                return {
                    "grant_type": "urn:ietf:params:oauth:grant-type:token-exchange",
                    "subject_token": user_token,
                    "subject_token_type": "urn:ietf:params:oauth:token-type:access_token",
                    "client_id": agent_id,
                    "client_secret": agent_secret,
                }

            no_caps = await client.post("/iam/v1/token", data=exchange_data())
            assert no_caps.status_code == 403
            assert no_caps.json()["error"]["code"] == "IAM_NO_CAPABILITIES"

            # 4. el dueño otorga capacidades
            grant = await client.post(
                f"/iam/v1/agents/{agent_id}/grants", json={"cap": CAP}, headers=auth_user
            )
            assert grant.status_code == 201, grant.text

            # 5. token exchange RFC 8693 con cadena act
            exchanged = await client.post("/iam/v1/token", data=exchange_data())
            assert exchanged.status_code == 200, exchanged.text
            agent_token = exchanged.json()["access_token"]

            jwks = await client.get("/iam/v1/jwks")
            from joserfc import jwt
            from joserfc.jwk import KeySet

            claims = jwt.decode(
                agent_token, KeySet.import_key_set(jwks.json()), algorithms=["RS256"]
            ).claims
            assert claims["sub"] == f"agent:{agent_id}"
            assert claims["act"]["sub"] == f"user:{principal_id}"
            assert claims["tenant"] == tenant

            auth_agent = {"Authorization": f"Bearer {agent_token}"}

            async def authorize(payload: dict[str, Any]) -> dict[str, Any]:
                resp = await client.post("/iam/v1/authorize", json=payload, headers=auth_agent)
                assert resp.status_code == 200, resp.text
                return dict(resp.json())

            # 6. PDP: permitido, no otorgado, denegado por glob, monto excedido
            assert (await authorize({"model": "sale.order", "operation": "read"}))["allowed"]
            not_granted = await authorize({"model": "stock.picking", "operation": "read"})
            assert not not_granted["allowed"]
            assert not_granted["reason"] == "CAP_NOT_GRANTED"
            denied = await authorize({"model": "res.users", "operation": "write"})
            assert not denied["allowed"]
            assert denied["reason"] == "CAP_DENIED"
            too_big = await authorize(
                {
                    "model": "sale.order",
                    "operation": "create",
                    "amount": {"currency": "CLP", "value": "9000000"},
                }
            )
            assert not too_big["allowed"]
            assert too_big["reason"] == "CAP_AMOUNT_EXCEEDED"

            # 7. operación que exige HITL
            needs_approval = await authorize({"model": "account.move", "operation": "action_post"})
            assert needs_approval["allowed"]
            assert needs_approval["requires_approval"] is True

            operation = {
                "model": "account.move",
                "operation": "action_post",
                "payload": {"move_id": 7},
            }
            idem = uuid.uuid4().hex
            created = await client.post(
                "/iam/v1/approvals",
                json={"operation": operation},
                headers={**auth_agent, "Idempotency-Key": idem},
            )
            assert created.status_code == 201, created.text
            approval_id = created.json()["approval_id"]

            # consumo antes de aprobar: pendiente y reintentable
            early = await client.post(
                f"/iam/v1/approvals/{approval_id}/consume",
                json={"operation": operation},
                headers=auth_agent,
            )
            assert early.status_code == 409
            assert early.json()["error"]["retryable"] is True

            approved = await client.post(
                f"/iam/v1/approvals/{approval_id}/approve", headers=auth_user
            )
            assert approved.status_code == 200, approved.text

            # operación alterada: rechazada aunque esté aprobada
            tampered = await client.post(
                f"/iam/v1/approvals/{approval_id}/consume",
                json={"operation": {**operation, "payload": {"move_id": 999}}},
                headers=auth_agent,
            )
            assert tampered.status_code == 409
            assert tampered.json()["error"]["code"] == "IAM_APPROVAL_MISMATCH"

            consumed = await client.post(
                f"/iam/v1/approvals/{approval_id}/consume",
                json={"operation": operation},
                headers=auth_agent,
            )
            assert consumed.status_code == 200, consumed.text
            assert consumed.json()["status"] == "consumed"

            twice = await client.post(
                f"/iam/v1/approvals/{approval_id}/consume",
                json={"operation": operation},
                headers=auth_agent,
            )
            assert twice.status_code == 409
            assert twice.json()["error"]["code"] == "IAM_APPROVAL_CONSUMED"

        # 8. auditoría encadenada íntegra tras todo el flujo
        from ordo_iam.audit import verify_chain
        from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

        engine = create_async_engine(e2e_db_url)
        maker = async_sessionmaker(engine, expire_on_commit=False)
        async with maker() as session:
            ok, broken = await verify_chain(session, tenant)
        await engine.dispose()
        assert ok, f"cadena de auditoría rota en {broken}"

    async def test_unprovisioned_user_cannot_login(
        self, iam_service: str, keycloak_admin_token: str
    ) -> None:
        """Keycloak autentica, pero ordo no conoce la identidad ⇒ 401."""
        email = f"e2e-ghost-{uuid.uuid4().hex[:8]}@acme.cl"
        password = uuid.uuid4().hex
        resp = await asyncio.to_thread(
            httpx.post,
            f"{KEYCLOAK_URL}/admin/realms/{REALM}/users",
            headers={"Authorization": f"Bearer {keycloak_admin_token}"},
            json={
                "username": email,
                "email": email,
                "emailVerified": True,
                "enabled": True,
                "firstName": "E2E",
                "lastName": "Test",
                "requiredActions": [],
                "attributes": {"tenant": "acme"},
                "credentials": [{"type": "password", "value": password, "temporary": False}],
            },
            timeout=10,
        )
        assert resp.status_code in (201, 409)
        token = await asyncio.to_thread(user_access_token, email, password)
        async with httpx.AsyncClient(base_url=iam_service, timeout=20) as client:
            me = await client.get("/iam/v1/me", headers={"Authorization": f"Bearer {token}"})
        assert me.status_code == 401
        assert me.json()["error"]["code"] == "IAM_UNKNOWN_IDENTITY"
