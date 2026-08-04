"""Enforcement de tokens (ADR-016): IAM real, token firmado, PDP y roles."""

import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi import FastAPI
from ordo_runtime.authz import PDPClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

pytestmark = pytest.mark.integration

REPO_ROOT = Path(__file__).resolve().parents[3]
TENANT = "tokencorp"


def _admin_dsn() -> str:
    import os

    pw = os.environ.get("POSTGRES_PASSWORD", "")
    return f"postgresql+asyncpg://ordo:{pw}@127.0.0.1:5432/ordo"


@pytest.fixture(scope="module")
async def iam_db_url() -> AsyncIterator[str]:
    db_name = f"ordo_authz_test_{uuid.uuid4().hex[:8]}"
    admin = create_async_engine(_admin_dsn(), isolation_level="AUTOCOMMIT")
    try:
        async with admin.connect() as conn:
            await conn.execute(text(f'CREATE DATABASE "{db_name}"'))
    except Exception:
        pytest.skip("Postgres no disponible (make up)")
    url = _admin_dsn().rsplit("/", 1)[0] + f"/{db_name}"
    from ordo_iam.migrations import upgrade_to_head

    await upgrade_to_head(url)
    yield url
    async with admin.connect() as conn:
        await conn.execute(text(f'DROP DATABASE IF EXISTS "{db_name}" WITH (FORCE)'))
    await admin.dispose()


@pytest.fixture
async def shop(core_db_url: str) -> AsyncIterator[dict[str, Any]]:
    """Tenant comercial con el MISMO nombre que el tenant del token."""
    from tests.integration.commercial import build_shop

    engine = create_async_engine(core_db_url)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    session: AsyncSession = maker()
    exists = (
        await session.execute(
            text("SELECT 1 FROM information_schema.schemata WHERE schema_name = :s"),
            {"s": f"t_{TENANT}"},
        )
    ).first()
    if exists:
        await session.execute(text(f'DROP SCHEMA "t_{TENANT}" CASCADE'))
        await session.commit()
    data = await build_shop(session, TENANT, modules_root=REPO_ROOT / "modules")
    data["tenant"] = TENANT
    yield data
    await session.close()
    await engine.dispose()


@pytest.fixture
async def iam_session(iam_db_url: str) -> AsyncIterator[AsyncSession]:
    engine = create_async_engine(iam_db_url)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    async with maker() as session:
        yield session
    await engine.dispose()


@pytest.fixture
async def seeded_user(iam_session: AsyncSession) -> uuid.UUID:
    """Roles comerciales cargados y un usuario con rol ventas."""
    import sys

    sys.path.insert(0, str(REPO_ROOT / "tools"))
    from ordo_iam.models import Role, RoleMember
    from ordo_iam.repository import PrincipalRepository
    from seed_iam_roles import seed
    from sqlalchemy import select

    await seed(iam_session, TENANT)
    repo = PrincipalRepository(iam_session)
    user = await repo.create_user(
        tenant=TENANT, email=f"v-{uuid.uuid4().hex[:6]}@acme.cl", display_name="V"
    )
    role = await iam_session.scalar(
        select(Role).where(Role.tenant == TENANT, Role.name == "ventas")
    )
    assert role is not None
    iam_session.add(RoleMember(role_id=role.id, principal_id=user.principal_id))
    await iam_session.commit()
    return user.principal_id


CAP = {
    "models": {
        "sale.order": ["read", "write", "create"],
        "res.partner": ["read"],
        "ir.model": ["read"],
    },
    "requires_approval": ["sale.order.action_invoice"],
    "deny": [],
}


def make_token(user_id: uuid.UUID, *, tenant: str = TENANT, cap: dict[str, Any] = CAP) -> str:
    from joserfc import jwt
    from ordo_iam.keys import issuer, signing_key

    key = signing_key()
    now = datetime.now(tz=UTC)
    claims = {
        "iss": issuer(),
        "aud": "ordo-api",
        "sub": f"agent:{uuid.uuid4()}",
        "act": {"sub": f"user:{user_id}"},
        "tenant": tenant,
        "cap": cap,
        "jti": uuid.uuid4().hex,
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(minutes=15)).timestamp()),
    }
    return jwt.encode({"alg": "RS256", "kid": key.kid}, claims, key)


@pytest.fixture
async def pdp(iam_session: AsyncSession) -> AsyncIterator[PDPClient]:
    """PDP real: la app IAM entera detrás de un transporte ASGI."""
    from ordo_iam.api import get_session, get_usage_counter
    from ordo_iam.main import app as iam_app
    from ordo_iam.pdp import InMemoryUsageCounter

    async def override_session() -> AsyncIterator[AsyncSession]:
        yield iam_session

    counter = InMemoryUsageCounter()
    iam_app.dependency_overrides[get_session] = override_session
    iam_app.dependency_overrides[get_usage_counter] = lambda: counter
    transport = httpx.ASGITransport(app=iam_app, raise_app_exceptions=False)
    client = httpx.AsyncClient(transport=transport, base_url="http://iam")
    try:
        yield PDPClient(client=client)
    finally:
        await client.aclose()
        iam_app.dependency_overrides.clear()


@pytest.fixture
async def api(shop: dict[str, Any], pdp: PDPClient) -> AsyncIterator[httpx.AsyncClient]:
    """App API fresca con enforcement instalado y datos del shop."""
    from ordo_api.actions import router as actions_router
    from ordo_api.authz import install_enforcement
    from ordo_api.deps import get_env, get_registry, get_session
    from ordo_api.meta import router as meta_router
    from ordo_api.records import router as records_router
    from ordo_api.reports import router as reports_router

    app = FastAPI()
    app.include_router(actions_router)
    app.include_router(reports_router)
    app.include_router(records_router)
    app.include_router(meta_router)
    install_enforcement(app, client=pdp)

    env = shop["env"]

    async def override_session() -> AsyncIterator[AsyncSession]:
        yield env.session

    app.dependency_overrides[get_session] = override_session
    app.dependency_overrides[get_registry] = lambda: env.registry
    app.dependency_overrides[get_env] = lambda: env
    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        yield client


class TestApiEnforcement:
    async def test_no_token_is_401(self, api: httpx.AsyncClient) -> None:
        response = await api.get("/api/v1/sale.order")
        assert response.status_code == 401
        assert response.json()["error"]["code"] == "AUTH_REQUIRED"

    async def test_valid_token_reads_its_model(
        self, api: httpx.AsyncClient, seeded_user: uuid.UUID
    ) -> None:
        token = make_token(seeded_user)
        response = await api.get("/api/v1/sale.order", headers={"Authorization": f"Bearer {token}"})
        assert response.status_code == 200, response.text

    async def test_cap_denies_models_outside_the_grant(
        self, api: httpx.AsyncClient, seeded_user: uuid.UUID
    ) -> None:
        token = make_token(seeded_user)
        response = await api.patch(
            "/api/v1/account.move/1",
            json={"values": {"ref": "x"}},
            headers={"Authorization": f"Bearer {token}", "Idempotency-Key": "k1"},
        )
        assert response.status_code == 403
        assert response.json()["error"]["code"] == "AUTH_DENIED"

    async def test_approval_required_surfaces_the_flow(
        self, api: httpx.AsyncClient, seeded_user: uuid.UUID
    ) -> None:
        token = make_token(seeded_user)
        response = await api.post(
            "/api/v1/sale.order/1/actions/action_invoice",
            json={"params": {}},
            headers={"Authorization": f"Bearer {token}", "Idempotency-Key": "k2"},
        )
        assert response.status_code == 403
        body = response.json()["error"]
        assert body["code"] == "IAM_APPROVAL_REQUIRED"
        assert "approvals" in body["hint"]

    async def test_header_contradicting_the_token_is_403(
        self, api: httpx.AsyncClient, seeded_user: uuid.UUID
    ) -> None:
        token = make_token(seeded_user)
        response = await api.get(
            "/api/v1/sale.order",
            headers={"Authorization": f"Bearer {token}", "X-Ordo-Tenant": "otra"},
        )
        assert response.status_code == 403
        assert response.json()["error"]["code"] == "AUTH_TENANT_MISMATCH"

    async def test_garbage_token_is_401(self, api: httpx.AsyncClient) -> None:
        response = await api.get("/api/v1/sale.order", headers={"Authorization": "Bearer basura"})
        assert response.status_code == 401

    async def test_pdp_down_fails_closed(self, shop: dict[str, Any]) -> None:
        from ordo_api.authz import install_enforcement
        from ordo_api.records import router as records_router

        dead = PDPClient(client=httpx.AsyncClient(base_url="http://127.0.0.1:9", timeout=0.2))
        app = FastAPI()
        app.include_router(records_router)
        install_enforcement(app, client=dead)
        transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
        async with httpx.AsyncClient(transport=transport, base_url="http://t") as client:
            response = await client.get("/api/v1/sale.order", headers={"Authorization": "Bearer x"})
        assert response.status_code == 503
        assert response.json()["error"]["code"] == "AUTH_PDP_UNAVAILABLE"


class TestMcpEnforcement:
    async def test_tools_call_requires_token_and_works_with_it(
        self, shop: dict[str, Any], pdp: PDPClient, seeded_user: uuid.UUID
    ) -> None:

        from ordo_mcp import deps as mcp_deps
        from ordo_mcp.main import app as mcp_app
        from ordo_mcp.main import set_pdp_client

        set_pdp_client(pdp)
        try:
            transport = httpx.ASGITransport(app=mcp_app, raise_app_exceptions=False)
            async with httpx.AsyncClient(transport=transport, base_url="http://m") as client:
                body = {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "tools/call",
                    "params": {
                        "name": "ordo_search",
                        "arguments": {"model": "sale.order"},
                    },
                }
                no_token = await client.post("/mcp", json=body)
                result = no_token.json()["result"]
                assert result["isError"]
                assert "AUTH_REQUIRED" in result["content"][0]["text"]

                # con token: pasa el enforcement y llega al handler (la base
                # del servicio no es la del shop; basta con que no sea 401/403)
                token = make_token(seeded_user)
                with pytest.MonkeyPatch.context() as mp:
                    mp.setenv(
                        "ORDO_DATABASE_URL",
                        shop["env"].session.bind.url.render_as_string(hide_password=False),
                    )
                    mp.setenv("ORDO_MODULES_PATH", str(REPO_ROOT / "modules"))
                    mp.setenv("ORDO_DB_ROLE", "")
                    mp.setattr(mcp_deps, "_engine", None)
                    mp.setattr(mcp_deps, "_registry", None)
                    with_token = await client.post(
                        "/mcp",
                        json=body,
                        headers={
                            "Authorization": f"Bearer {token}",
                            "X-Ordo-Tenant": TENANT,
                        },
                    )
                    payload = with_token.json()["result"]
                    text_payload = payload["content"][0]["text"]
                    assert "AUTH_REQUIRED" not in text_payload
                    assert "AUTH_DENIED" not in text_payload
                if mcp_deps._engine is not None:
                    await mcp_deps._engine.dispose()
                    mcp_deps._engine = None
                    mcp_deps._registry = None
        finally:
            set_pdp_client(None)
