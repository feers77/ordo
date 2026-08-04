"""Sandbox efímero (F3-03 §3): clonar, escribir, borrar sin tocar el origen."""

import uuid
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import httpx
import pytest
from ordo_core.sandbox import (
    SANDBOX_MARKER,
    SandboxError,
    create_sandbox,
    drop_sandbox,
    ensure_registry_table,
    purge_expired,
)
from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from tests.integration.commercial import build_shop

pytestmark = pytest.mark.integration

MODULES_ROOT = Path(__file__).resolve().parents[3] / "modules"


@pytest.fixture
async def admin_engine(core_db_url: str, app_role_ready: None) -> AsyncIterator[AsyncEngine]:
    """Motor con el rol dueño: el sandbox es DDL y ordo_app no lo tiene."""
    from ordo_api.sandbox import set_admin_engine

    engine = create_async_engine(core_db_url)
    set_admin_engine(engine)
    yield engine
    set_admin_engine(None)
    await engine.dispose()


@pytest.fixture
async def admin(admin_engine: AsyncEngine) -> AsyncIterator[AsyncSession]:
    maker = async_sessionmaker(admin_engine, expire_on_commit=False)
    async with maker() as session:
        yield session


@pytest.fixture
async def shop(admin: AsyncSession) -> dict[str, Any]:
    """Tenant real con datos: partners, cuentas, diarios."""
    tenant = f"sbx{uuid.uuid4().hex[:8]}"
    data = await build_shop(admin, tenant, modules_root=MODULES_ROOT)
    data["tenant"] = tenant
    await ensure_registry_table(admin)
    return data


async def _count(session: AsyncSession, tenant: str, table: str) -> int:
    sql = f'SELECT count(*) FROM "t_{tenant}"."{table}"'
    return int((await session.execute(text(sql))).scalar_one())


@pytest.fixture
async def client(shop: dict[str, Any]) -> AsyncIterator[httpx.AsyncClient]:
    """App mínima con el router de sandbox y el Environment del tenant."""
    from ordo_api.deps import get_env
    from ordo_api.sandbox import router
    from ordo_runtime import create_app

    class FakeEnv:
        tenant = shop["tenant"]

    app = create_app("api-sandbox-test")
    app.include_router(router)
    app.dependency_overrides[get_env] = lambda: FakeEnv()
    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


class TestKernelSandbox:
    async def test_clone_copies_structure_and_data(
        self, admin: AsyncSession, shop: dict[str, Any]
    ) -> None:
        source = shop["tenant"]
        before = await _count(admin, source, "res_partner")

        info = await create_sandbox(admin, source, ttl_hours=1)

        assert SANDBOX_MARKER in info["tenant"]
        assert info["source_tenant"] == source
        assert info["tables"] > 0
        exists = (
            await admin.execute(
                text("SELECT 1 FROM information_schema.schemata WHERE schema_name = :s"),
                {"s": f"t_{info['tenant']}"},
            )
        ).first()
        assert exists is not None
        assert await _count(admin, info["tenant"], "res_partner") == before

        await drop_sandbox(admin, info["tenant"])

    async def test_sandbox_is_writable_and_isolated(
        self, admin: AsyncSession, shop: dict[str, Any]
    ) -> None:
        source = shop["tenant"]
        before = await _count(admin, source, "res_partner")
        info = await create_sandbox(admin, source)
        sandbox = info["tenant"]

        # Sin secuencia ni PK propias este INSERT fallaría con id nulo.
        await admin.execute(
            text(f'INSERT INTO "t_{sandbox}"."res_partner" (name) VALUES (:n)'),
            {"n": "Solo en el sandbox"},
        )
        await admin.commit()

        assert await _count(admin, sandbox, "res_partner") == before + 1
        assert await _count(admin, source, "res_partner") == before

        await drop_sandbox(admin, sandbox)
        # Borrar el sandbox no toca el origen.
        assert await _count(admin, source, "res_partner") == before
        left = (
            await admin.execute(
                text("SELECT 1 FROM information_schema.schemata WHERE schema_name = :s"),
                {"s": f"t_{sandbox}"},
            )
        ).first()
        assert left is None

    async def test_drop_refuses_a_real_tenant(
        self, admin: AsyncSession, shop: dict[str, Any]
    ) -> None:
        with pytest.raises(SandboxError) as excinfo:
            await drop_sandbox(admin, shop["tenant"])
        assert excinfo.value.code == "SANDBOX_REFUSED"
        # El schema del tenant sigue intacto.
        assert await _count(admin, shop["tenant"], "res_partner") > 0

    async def test_sandbox_does_not_clone_a_sandbox(
        self, admin: AsyncSession, shop: dict[str, Any]
    ) -> None:
        info = await create_sandbox(admin, shop["tenant"])
        with pytest.raises(SandboxError) as excinfo:
            await create_sandbox(admin, info["tenant"])
        assert excinfo.value.code == "SANDBOX_NESTED"
        await drop_sandbox(admin, info["tenant"])

    async def test_unknown_source_is_rejected(self, admin: AsyncSession) -> None:
        await ensure_registry_table(admin)
        with pytest.raises(SandboxError) as excinfo:
            await create_sandbox(admin, f"nadie{uuid.uuid4().hex[:6]}")
        assert excinfo.value.code == "SANDBOX_SOURCE_NOT_FOUND"

    async def test_purge_expired_collects_the_dead(
        self, admin: AsyncSession, shop: dict[str, Any]
    ) -> None:
        info = await create_sandbox(admin, shop["tenant"], ttl_hours=0)
        sandbox = info["tenant"]

        dropped = await purge_expired(admin)

        assert sandbox in dropped
        left = (
            await admin.execute(
                text("SELECT 1 FROM information_schema.schemata WHERE schema_name = :s"),
                {"s": f"t_{sandbox}"},
            )
        ).first()
        assert left is None
        assert await _count(admin, shop["tenant"], "res_partner") > 0


class TestSandboxEndpoints:
    async def test_create_list_and_delete(
        self, client: httpx.AsyncClient, admin: AsyncSession, shop: dict[str, Any]
    ) -> None:
        created = await client.post("/api/v1/sandbox", json={"ttl_hours": 2})
        assert created.status_code == 201, created.text
        body = created.json()
        assert body["source_tenant"] == shop["tenant"]
        assert SANDBOX_MARKER in body["tenant"]
        assert body["tables"] > 0

        listed = await client.get("/api/v1/sandbox")
        assert listed.status_code == 200, listed.text
        assert [row["tenant"] for row in listed.json()["sandboxes"]] == [body["tenant"]]

        deleted = await client.delete(f"/api/v1/sandbox/{body['tenant']}")
        assert deleted.status_code == 200, deleted.text
        assert deleted.json() == {"dropped": body["tenant"]}
        assert await _count(admin, shop["tenant"], "res_partner") > 0

    async def test_delete_of_a_foreign_sandbox_is_forbidden(
        self, client: httpx.AsyncClient
    ) -> None:
        resp = await client.delete("/api/v1/sandbox/otro_sb_1234")
        assert resp.status_code == 403, resp.text
        assert resp.json()["error"]["code"] == "SANDBOX_FOREIGN"
