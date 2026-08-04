"""Tests del token exchange RFC 8693 (F1-03) — escritos antes de implementar.

Helpers compartidos (emisor simulado, cliente, setup de agente) en conftest.py.
"""

import uuid

import httpx
import pytest
from joserfc import jwt
from joserfc.jwk import KeySet
from ordo_iam.repository import PrincipalRepository
from sqlalchemy.ext.asyncio import AsyncSession

pytestmark = pytest.mark.integration


class TestTokenExchange:
    async def test_happy_path_issues_verifiable_agent_token(
        self,
        api_client: httpx.AsyncClient,
        session: AsyncSession,
        helpers,  # type: ignore[no-untyped-def]
    ) -> None:
        owner_token, agent_id, secret = await helpers.setup_agent(
            api_client, session, email="o1@acme.cl"
        )
        resp = await helpers.do_exchange(
            api_client, subject_token=owner_token, agent_id=agent_id, secret=secret
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["issued_token_type"] == "urn:ietf:params:oauth:token-type:access_token"
        assert body["token_type"] == "Bearer"
        assert body["expires_in"] <= 900

        jwks_resp = await api_client.get("/iam/v1/jwks")
        assert jwks_resp.status_code == 200
        keyset = KeySet.import_key_set(jwks_resp.json())
        decoded = jwt.decode(body["access_token"], keyset, algorithms=["RS256"])
        claims = decoded.claims
        assert claims["sub"] == f"agent:{agent_id}"
        assert claims["act"]["sub"].startswith("user:")
        assert claims["tenant"] == "acme"
        assert claims["cap"]["models"] == {"sale.order": ["create", "read"]}
        assert claims["jti"]

    async def test_wrong_secret_rejected(
        self,
        api_client: httpx.AsyncClient,
        session: AsyncSession,
        helpers,  # type: ignore[no-untyped-def]
    ) -> None:
        owner_token, agent_id, _ = await helpers.setup_agent(
            api_client, session, email="o2@acme.cl"
        )
        resp = await helpers.do_exchange(
            api_client, subject_token=owner_token, agent_id=agent_id, secret="malo"
        )
        assert resp.status_code == 401
        assert resp.json()["error"]["code"] == "IAM_AGENT_AUTH_FAILED"

    async def test_unknown_agent_rejected(
        self,
        api_client: httpx.AsyncClient,
        session: AsyncSession,
        helpers,  # type: ignore[no-untyped-def]
    ) -> None:
        owner_token, _, secret = await helpers.setup_agent(api_client, session, email="o3@acme.cl")
        resp = await helpers.do_exchange(
            api_client, subject_token=owner_token, agent_id=str(uuid.uuid4()), secret=secret
        )
        assert resp.status_code == 401

    async def test_suspended_agent_rejected(
        self,
        api_client: httpx.AsyncClient,
        session: AsyncSession,
        helpers,  # type: ignore[no-untyped-def]
    ) -> None:
        owner_token, agent_id, secret = await helpers.setup_agent(
            api_client, session, email="o4@acme.cl"
        )
        repo = PrincipalRepository(session)
        await repo.suspend_principal(uuid.UUID(agent_id))
        resp = await helpers.do_exchange(
            api_client, subject_token=owner_token, agent_id=agent_id, secret=secret
        )
        assert resp.status_code == 403
        assert resp.json()["error"]["code"] == "IAM_AGENT_SUSPENDED"

    async def test_subject_must_be_owner(
        self,
        api_client: httpx.AsyncClient,
        session: AsyncSession,
        helpers,  # type: ignore[no-untyped-def]
    ) -> None:
        _, agent_id, secret = await helpers.setup_agent(api_client, session, email="o5@acme.cl")
        repo = PrincipalRepository(session)
        await repo.create_user(tenant="acme", email="otra@acme.cl", display_name="Otra")
        other_token = helpers.kc_token("kc-otra@acme.cl", "otra@acme.cl")
        resp = await helpers.do_exchange(
            api_client, subject_token=other_token, agent_id=agent_id, secret=secret
        )
        assert resp.status_code == 403
        assert resp.json()["error"]["code"] == "IAM_DELEGATION_NOT_ALLOWED"

    async def test_no_effective_grants_rejected(
        self,
        api_client: httpx.AsyncClient,
        session: AsyncSession,
        helpers,  # type: ignore[no-untyped-def]
    ) -> None:
        owner_token, agent_id, secret = await helpers.setup_agent(
            api_client, session, email="o6@acme.cl", with_grant=False
        )
        resp = await helpers.do_exchange(
            api_client, subject_token=owner_token, agent_id=agent_id, secret=secret
        )
        assert resp.status_code == 403
        assert resp.json()["error"]["code"] == "IAM_NO_CAPABILITIES"

    async def test_bad_grant_type_rejected(
        self,
        api_client: httpx.AsyncClient,
        session: AsyncSession,
        helpers,  # type: ignore[no-untyped-def]
    ) -> None:
        owner_token, agent_id, secret = await helpers.setup_agent(
            api_client, session, email="o7@acme.cl"
        )
        resp = await helpers.do_exchange(
            api_client,
            subject_token=owner_token,
            agent_id=agent_id,
            secret=secret,
            grant_type="client_credentials",
        )
        assert resp.status_code == 400
        assert resp.json()["error"]["code"] == "IAM_UNSUPPORTED_GRANT"

    async def test_agent_token_cannot_be_re_exchanged(
        self,
        api_client: httpx.AsyncClient,
        session: AsyncSession,
        helpers,  # type: ignore[no-untyped-def]
    ) -> None:
        owner_token, agent_id, secret = await helpers.setup_agent(
            api_client, session, email="o8@acme.cl"
        )
        first = await helpers.do_exchange(
            api_client, subject_token=owner_token, agent_id=agent_id, secret=secret
        )
        agent_token = first.json()["access_token"]
        resp = await helpers.do_exchange(
            api_client, subject_token=agent_token, agent_id=agent_id, secret=secret
        )
        assert resp.status_code == 401
        assert resp.json()["error"]["code"] in ("IAM_TOKEN_INVALID", "IAM_TOKEN_EXPIRED")

    async def test_registration_requires_auth(self, api_client: httpx.AsyncClient) -> None:
        resp = await api_client.post("/iam/v1/agents", json={"display_name": "x", "model": "m"})
        assert resp.status_code == 401

    async def test_grant_only_by_owner(
        self,
        api_client: httpx.AsyncClient,
        session: AsyncSession,
        helpers,  # type: ignore[no-untyped-def]
    ) -> None:
        _, agent_id, _ = await helpers.setup_agent(api_client, session, email="o9@acme.cl")
        repo = PrincipalRepository(session)
        await repo.create_user(tenant="acme", email="intrusa@acme.cl", display_name="I")
        intruder_token = helpers.kc_token("kc-intrusa@acme.cl", "intrusa@acme.cl")
        resp = await api_client.post(
            f"/iam/v1/agents/{agent_id}/grants",
            json={"cap": {"models": {"sale.order": ["read"]}}},
            headers={"Authorization": f"Bearer {intruder_token}"},
        )
        assert resp.status_code == 403
