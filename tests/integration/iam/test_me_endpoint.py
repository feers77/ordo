"""Test end-to-end del endpoint /iam/v1/me (F1-02) con emisor simulado."""

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
KEY = RSAKey.generate_key(2048, {"kid": "e2e-key", "alg": "RS256"})


def token(sub: str, email: str, tenant: str = "acme") -> str:
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
    return jwt.encode({"alg": "RS256", "kid": KEY.kid}, claims, KEY)


@pytest.fixture
async def client(session: AsyncSession) -> AsyncIterator[httpx.AsyncClient]:
    from ordo_iam.main import app

    async def override_session() -> AsyncIterator[AsyncSession]:
        yield session

    app.dependency_overrides[get_session] = override_session
    app.dependency_overrides[get_verifier] = lambda: OIDCVerifier(
        issuer=ISSUER, audience=AUDIENCE, static_jwks=KeySet([KEY])
    )
    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
    try:
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
            yield c
    finally:
        app.dependency_overrides.clear()


class TestMeEndpoint:
    async def test_me_ok(self, session: AsyncSession, client: httpx.AsyncClient) -> None:
        repo = PrincipalRepository(session)
        await repo.create_user(tenant="acme", email="me@acme.cl", display_name="Me")
        resp = await client.get(
            "/iam/v1/me", headers={"Authorization": f"Bearer {token('kc-me', 'me@acme.cl')}"}
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["tenant"] == "acme"
        assert body["email"] == "me@acme.cl"
        assert body["type"] == "user"

    async def test_me_without_token_401(self, client: httpx.AsyncClient) -> None:
        resp = await client.get("/iam/v1/me")
        assert resp.status_code == 401
        assert resp.json()["error"]["code"] == "IAM_TOKEN_INVALID"

    async def test_me_bad_token_401(self, client: httpx.AsyncClient) -> None:
        resp = await client.get("/iam/v1/me", headers={"Authorization": "Bearer basura"})
        assert resp.status_code == 401

    async def test_me_unknown_identity_401(self, client: httpx.AsyncClient) -> None:
        resp = await client.get(
            "/iam/v1/me",
            headers={"Authorization": f"Bearer {token('kc-x', 'fantasma@acme.cl')}"},
        )
        assert resp.status_code == 401
        assert resp.json()["error"]["code"] == "IAM_UNKNOWN_IDENTITY"
