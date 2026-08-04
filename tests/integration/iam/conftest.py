"""Fixtures de integración IAM: base de datos real, schema desde Alembic."""

import os
import uuid
from collections.abc import AsyncIterator

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

ADMIN_DSN = os.environ.get(
    "IAM_TEST_ADMIN_DSN",
    "postgresql+asyncpg://ordo:{pw}@127.0.0.1:5432/ordo",
)


def _admin_dsn() -> str:
    pw = os.environ.get("POSTGRES_PASSWORD", "")
    return ADMIN_DSN.format(pw=pw)


@pytest.fixture(scope="session")
def test_db_name() -> str:
    return f"ordo_iam_test_{uuid.uuid4().hex[:8]}"


@pytest.fixture(scope="session")
async def test_db_url(test_db_name: str) -> AsyncIterator[str]:
    admin = create_async_engine(_admin_dsn(), isolation_level="AUTOCOMMIT")
    try:
        async with admin.connect() as conn:
            from sqlalchemy import text

            await conn.execute(text(f'CREATE DATABASE "{test_db_name}"'))
    except Exception:
        pytest.skip("Postgres no disponible (levanta el stack: make up)")
    url = _admin_dsn().rsplit("/", 1)[0] + f"/{test_db_name}"
    yield url
    async with admin.connect() as conn:
        from sqlalchemy import text

        await conn.execute(text(f'DROP DATABASE IF EXISTS "{test_db_name}" WITH (FORCE)'))
    await admin.dispose()


@pytest.fixture(scope="session")
async def migrated_db(test_db_url: str) -> str:
    from ordo_iam.migrations import upgrade_to_head

    await upgrade_to_head(test_db_url)
    return test_db_url


@pytest.fixture
async def session(migrated_db: str) -> AsyncIterator[AsyncSession]:
    engine = create_async_engine(migrated_db)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    async with maker() as s:
        yield s
    await engine.dispose()


# ---------------------------------------------------------------------------
# Helpers compartidos: emisor Keycloak simulado + cliente HTTP con overrides
# ---------------------------------------------------------------------------

import time  # noqa: E402
from typing import Any  # noqa: E402

import httpx  # noqa: E402
from joserfc import jwt  # noqa: E402
from joserfc.jwk import KeySet, RSAKey  # noqa: E402

KC_ISSUER = "http://idp.test/realms/ordo"
KC_AUDIENCE = "ordo-api"
KC_KEY = RSAKey.generate_key(2048, {"kid": "kc-key", "alg": "RS256"})
EXCHANGE_GRANT_TYPE = "urn:ietf:params:oauth:grant-type:token-exchange"
DEFAULT_CAP = {"models": {"sale.order": ["read", "create"]}}


def kc_token(sub: str, email: str, tenant: str = "acme") -> str:
    now = int(time.time())
    claims: dict[str, Any] = {
        "iss": KC_ISSUER,
        "aud": KC_AUDIENCE,
        "sub": sub,
        "iat": now,
        "exp": now + 300,
        "email": email,
        "email_verified": True,
        "tenant": tenant,
    }
    return jwt.encode({"alg": "RS256", "kid": KC_KEY.kid}, claims, KC_KEY)


@pytest.fixture
async def api_client(session: AsyncSession) -> AsyncIterator[httpx.AsyncClient]:
    from ordo_iam.api import get_verifier
    from ordo_iam.db import get_session
    from ordo_iam.main import app
    from ordo_iam.oidc import OIDCVerifier

    async def override_session() -> AsyncIterator[AsyncSession]:
        yield session

    app.dependency_overrides[get_session] = override_session
    app.dependency_overrides[get_verifier] = lambda: OIDCVerifier(
        issuer=KC_ISSUER, audience=KC_AUDIENCE, static_jwks=KeySet([KC_KEY])
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
    cap: dict[str, Any] | None = None,
) -> tuple[str, str, str]:
    """Crea owner + agente vía API; devuelve (owner_token, agent_id, agent_secret)."""
    from ordo_iam.repository import PrincipalRepository

    repo = PrincipalRepository(session)
    await repo.create_user(tenant="acme", email=email, display_name="Owner")
    owner_token = kc_token(f"kc-{email}", email)
    resp = await client.post(
        "/iam/v1/agents",
        json={"display_name": "bot", "model": "agente-v1"},
        headers={"Authorization": f"Bearer {owner_token}"},
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    agent_id, secret = body["agent_id"], body["agent_secret"]
    if with_grant:
        resp = await client.post(
            f"/iam/v1/agents/{agent_id}/grants",
            json={"cap": cap or DEFAULT_CAP},
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
    grant_type: str = EXCHANGE_GRANT_TYPE,
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


class Helpers:
    kc_token = staticmethod(kc_token)
    setup_agent = staticmethod(setup_agent)
    do_exchange = staticmethod(do_exchange)


@pytest.fixture
def helpers() -> type[Helpers]:
    return Helpers
