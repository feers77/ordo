"""Tests del token exchange RFC 8693 (F1-03) — escritos antes de implementar."""

import time
from collections.abc import AsyncIterator
from typing import Any

import httpx
import pytest
from joserfc import jwt
from joserfc.jwk import KeySet, RSAKey
from ordo_iam.api import get_verifier
from ordo_iam.db import get_session
from ordo_iam.oidc import OIDCVerifier
from ordo_iam.repository import PrincipalRepository
from sqlalchemy.ext.asyncio import AsyncSession

pytestmark = pytest.mark.integration

ISSUER = "http://idp.test/realms/ordo"
AUDIENCE = "ordo-api"
KC_KEY = RSAKey.generate_key(2048, {"kid": "kc-key", "alg": "RS256"})
GRANT_TYPE = "urn:ietf:params:oauth:grant-type:token-exchange"

CAP = {"models": {"sale.order": ["read", "create"]}}


def kc_token(sub: str, email: str, tenant: str = "acme") -> str:
    now = int(time.time())
    claims: dict[str, Any] = {
        "iss": ISSUER,
        "aud": AUDIENCE,
        "sub": sub,
        "iat": now,
        "exp": now + 300,
        "email": email,
        "email_verified": True,
        "tenant": tenant,
    }
    return jwt.encode({"alg": "RS256", "kid": KC_KEY.kid}, claims, KC_KEY)


@pytest.fixture
async def client(session: AsyncSession) -> AsyncIterator[httpx.AsyncClient]:
    from ordo_iam.main import app

    async def override_session() -> AsyncIterator[AsyncSession]:
        yield session

    app.dependency_overrides[get_session] = override_session
    app.dependency_overrides[get_verifier] = lambda: OIDCVerifier(
        issuer=ISSUER, audience=AUDIENCE, static_jwks=KeySet([KC_KEY])
    )
    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
    try:
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
            yield c
    finally:
        app.dependency_overrides.clear()


async def setup_agent(
    client: httpx.AsyncClient,
    session: AsyncSession,
    *,
    email: str,
    with_grant: bool = True,
) -> tuple[str, str, str]:
    """Crea owner + agente vía API; devuelve (owner_token, agent_id, agent_secret)."""
    repo = PrincipalRepository(session)
    await repo.create_user(tenant="acme", email=email, display_name="Owner")
    owner_token = kc_token(f"kc-{email}", email)
    resp = await client.post(
        "/iam/v1/agents",
        json={"display_name": "bot", "model": "claude-fable-5"},
        headers={"Authorization": f"Bearer {owner_token}"},
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    agent_id, secret = body["agent_id"], body["agent_secret"]
    if with_grant:
        resp = await client.post(
            f"/iam/v1/agents/{agent_id}/grants",
            json={"cap": CAP},
            headers={"Authorization": f"Bearer {owner_token}"},
        )
        assert resp.status_code == 201, resp.text
    return owner_token, agent_id, secret


async def do_exchange(
    client: httpx.AsyncClient,
    *,
    subject_token: str,
    agent_id: str,
    secret: str,
    grant_type: str = GRANT_TYPE,
) -> httpx.Response:
    return await client.post(
        "/iam/v1/token",
        data={
            "grant_type": grant_type,
            "subject_token": subject_token,
            "subject_token_type": "urn:ietf:params:oauth:token-type:access_token",
            "client_id": agent_id,
            "client_secret": secret,
        },
    )


class TestTokenExchange:
    async def test_happy_path_issues_verifiable_agent_token(
        self, client: httpx.AsyncClient, session: AsyncSession
    ) -> None:
        owner_token, agent_id, secret = await setup_agent(client, session, email="o1@acme.cl")
        resp = await do_exchange(
            client, subject_token=owner_token, agent_id=agent_id, secret=secret
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["issued_token_type"] == "urn:ietf:params:oauth:token-type:access_token"
        assert body["token_type"] == "Bearer"
        assert body["expires_in"] <= 900

        jwks_resp = await client.get("/iam/v1/jwks")
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
        self, client: httpx.AsyncClient, session: AsyncSession
    ) -> None:
        owner_token, agent_id, _ = await setup_agent(client, session, email="o2@acme.cl")
        resp = await do_exchange(
            client, subject_token=owner_token, agent_id=agent_id, secret="malo"
        )
        assert resp.status_code == 401
        assert resp.json()["error"]["code"] == "IAM_AGENT_AUTH_FAILED"

    async def test_unknown_agent_rejected(
        self, client: httpx.AsyncClient, session: AsyncSession
    ) -> None:
        owner_token, _, secret = await setup_agent(client, session, email="o3@acme.cl")
        import uuid

        resp = await do_exchange(
            client, subject_token=owner_token, agent_id=str(uuid.uuid4()), secret=secret
        )
        assert resp.status_code == 401

    async def test_suspended_agent_rejected(
        self, client: httpx.AsyncClient, session: AsyncSession
    ) -> None:
        import uuid

        owner_token, agent_id, secret = await setup_agent(client, session, email="o4@acme.cl")
        repo = PrincipalRepository(session)
        await repo.suspend_principal(uuid.UUID(agent_id))
        resp = await do_exchange(
            client, subject_token=owner_token, agent_id=agent_id, secret=secret
        )
        assert resp.status_code == 403
        assert resp.json()["error"]["code"] == "IAM_AGENT_SUSPENDED"

    async def test_subject_must_be_owner(
        self, client: httpx.AsyncClient, session: AsyncSession
    ) -> None:
        _, agent_id, secret = await setup_agent(client, session, email="o5@acme.cl")
        repo = PrincipalRepository(session)
        await repo.create_user(tenant="acme", email="otra@acme.cl", display_name="Otra")
        other_token = kc_token("kc-otra@acme.cl", "otra@acme.cl")
        resp = await do_exchange(
            client, subject_token=other_token, agent_id=agent_id, secret=secret
        )
        assert resp.status_code == 403
        assert resp.json()["error"]["code"] == "IAM_DELEGATION_NOT_ALLOWED"

    async def test_no_effective_grants_rejected(
        self, client: httpx.AsyncClient, session: AsyncSession
    ) -> None:
        owner_token, agent_id, secret = await setup_agent(
            client, session, email="o6@acme.cl", with_grant=False
        )
        resp = await do_exchange(
            client, subject_token=owner_token, agent_id=agent_id, secret=secret
        )
        assert resp.status_code == 403
        assert resp.json()["error"]["code"] == "IAM_NO_CAPABILITIES"

    async def test_bad_grant_type_rejected(
        self, client: httpx.AsyncClient, session: AsyncSession
    ) -> None:
        owner_token, agent_id, secret = await setup_agent(client, session, email="o7@acme.cl")
        resp = await do_exchange(
            client,
            subject_token=owner_token,
            agent_id=agent_id,
            secret=secret,
            grant_type="client_credentials",
        )
        assert resp.status_code == 400
        assert resp.json()["error"]["code"] == "IAM_UNSUPPORTED_GRANT"

    async def test_agent_token_cannot_be_re_exchanged(
        self, client: httpx.AsyncClient, session: AsyncSession
    ) -> None:
        owner_token, agent_id, secret = await setup_agent(client, session, email="o8@acme.cl")
        first = await do_exchange(
            client, subject_token=owner_token, agent_id=agent_id, secret=secret
        )
        agent_token = first.json()["access_token"]
        resp = await do_exchange(
            client, subject_token=agent_token, agent_id=agent_id, secret=secret
        )
        assert resp.status_code == 401
        assert resp.json()["error"]["code"] in ("IAM_TOKEN_INVALID", "IAM_TOKEN_EXPIRED")

    async def test_registration_requires_auth(self, client: httpx.AsyncClient) -> None:
        resp = await client.post("/iam/v1/agents", json={"display_name": "x", "model": "m"})
        assert resp.status_code == 401

    async def test_grant_only_by_owner(
        self, client: httpx.AsyncClient, session: AsyncSession
    ) -> None:
        _, agent_id, _ = await setup_agent(client, session, email="o9@acme.cl")
        repo = PrincipalRepository(session)
        await repo.create_user(tenant="acme", email="intrusa@acme.cl", display_name="I")
        intruder_token = kc_token("kc-intrusa@acme.cl", "intrusa@acme.cl")
        resp = await client.post(
            f"/iam/v1/agents/{agent_id}/grants",
            json={"cap": CAP},
            headers={"Authorization": f"Bearer {intruder_token}"},
        )
        assert resp.status_code == 403
