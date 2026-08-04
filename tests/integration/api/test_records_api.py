"""Tests de la API genérica de registros (F2-04)."""

import uuid
from collections.abc import AsyncIterator
from typing import Any

import httpx
import pytest
from ordo_core import Environment
from sqlalchemy.ext.asyncio import AsyncSession

from tests.integration.core.helpers import make_partner_env, partner_registry

pytestmark = pytest.mark.integration

TENANT = "apitest"


@pytest.fixture
async def env(core_session: AsyncSession) -> Environment:
    return await make_partner_env(core_session, TENANT)


@pytest.fixture
async def client(env: Environment) -> AsyncIterator[httpx.AsyncClient]:
    from ordo_api.deps import get_env, get_registry, get_session
    from ordo_api.main import app

    async def override_session() -> AsyncIterator[AsyncSession]:
        yield env.session

    app.dependency_overrides[get_session] = override_session
    app.dependency_overrides[get_registry] = partner_registry
    app.dependency_overrides[get_env] = lambda: env
    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
    try:
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
            yield c
    finally:
        app.dependency_overrides.clear()


def headers(key: str | None = None) -> dict[str, str]:
    out = {"X-Ordo-Tenant": TENANT}
    if key:
        out["Idempotency-Key"] = key
    return out


class TestCrudEndpoints:
    async def test_create_and_read(self, client: httpx.AsyncClient) -> None:
        resp = await client.post(
            "/api/v1/res.partner",
            json={"values": {"name": "ACME"}},
            headers=headers(uuid.uuid4().hex),
        )
        assert resp.status_code == 201, resp.text
        record_id = resp.json()["ids"][0]

        read = await client.get(f"/api/v1/res.partner/{record_id}", headers=headers())
        assert read.status_code == 200
        assert read.json()["name"] == "ACME"

    async def test_search_with_domain(self, client: httpx.AsyncClient) -> None:
        for name in ("Alfa", "Beta"):
            await client.post(
                "/api/v1/res.partner",
                json={"values": {"name": name}},
                headers=headers(uuid.uuid4().hex),
            )
        resp = await client.get(
            '/api/v1/res.partner?domain=[["name","=","Alfa"]]&fields=name',
            headers=headers(),
        )
        assert resp.status_code == 200, resp.text
        assert [row["name"] for row in resp.json()["rows"]] == ["Alfa"]

    async def test_patch_updates(self, client: httpx.AsyncClient) -> None:
        created = await client.post(
            "/api/v1/res.partner",
            json={"values": {"name": "ACME"}},
            headers=headers(uuid.uuid4().hex),
        )
        record_id = created.json()["ids"][0]
        resp = await client.patch(
            f"/api/v1/res.partner/{record_id}",
            json={"values": {"ref": "C-1"}},
            headers=headers(uuid.uuid4().hex),
        )
        assert resp.status_code == 200, resp.text
        read = await client.get(f"/api/v1/res.partner/{record_id}", headers=headers())
        assert read.json()["ref"] == "C-1"

    async def test_delete_removes(self, client: httpx.AsyncClient) -> None:
        created = await client.post(
            "/api/v1/res.partner",
            json={"values": {"name": "Temporal"}},
            headers=headers(uuid.uuid4().hex),
        )
        record_id = created.json()["ids"][0]
        resp = await client.delete(
            f"/api/v1/res.partner/{record_id}", headers=headers(uuid.uuid4().hex)
        )
        assert resp.status_code == 200
        read = await client.get(f"/api/v1/res.partner/{record_id}", headers=headers())
        assert read.status_code == 404

    async def test_unknown_model_404(self, client: httpx.AsyncClient) -> None:
        resp = await client.get("/api/v1/no.existe", headers=headers())
        assert resp.status_code == 404
        assert resp.json()["error"]["code"] == "MODEL_NOT_FOUND"

    async def test_validation_error_payload(self, client: httpx.AsyncClient) -> None:
        resp = await client.post(
            "/api/v1/res.partner",
            json={"values": {"ref": "sin nombre"}},
            headers=headers(uuid.uuid4().hex),
        )
        assert resp.status_code == 422
        error = resp.json()["error"]
        assert error["code"] == "FIELD_REQUIRED"
        assert "docs_url" in error


class TestIdempotencyEndpoint:
    async def test_write_requires_idempotency_key(self, client: httpx.AsyncClient) -> None:
        resp = await client.post(
            "/api/v1/res.partner", json={"values": {"name": "X"}}, headers=headers()
        )
        assert resp.status_code == 400
        assert resp.json()["error"]["code"] == "IDEMPOTENCY_KEY_REQUIRED"

    async def test_same_key_replays_response(self, client: httpx.AsyncClient) -> None:
        key = uuid.uuid4().hex
        payload: dict[str, Any] = {"values": {"name": "Única"}}
        first = await client.post("/api/v1/res.partner", json=payload, headers=headers(key))
        second = await client.post("/api/v1/res.partner", json=payload, headers=headers(key))
        assert first.json()["ids"] == second.json()["ids"]

        listing = await client.get(
            '/api/v1/res.partner?domain=[["name","=","Única"]]&fields=name', headers=headers()
        )
        assert len(listing.json()["rows"]) == 1  # no se creó dos veces

    async def test_same_key_different_payload_conflicts(self, client: httpx.AsyncClient) -> None:
        key = uuid.uuid4().hex
        await client.post(
            "/api/v1/res.partner", json={"values": {"name": "A"}}, headers=headers(key)
        )
        resp = await client.post(
            "/api/v1/res.partner", json={"values": {"name": "B"}}, headers=headers(key)
        )
        assert resp.status_code == 409
        assert resp.json()["error"]["code"] == "IDEMPOTENCY_KEY_REUSED"


class TestDryRunEndpoint:
    async def test_dry_run_create_does_not_persist(self, client: httpx.AsyncClient) -> None:
        resp = await client.post(
            "/api/v1/res.partner?dry_run=true",
            json={"values": {"name": "Simulada"}},
            headers=headers(),
        )
        assert resp.status_code == 201, resp.text
        assert resp.json()["would_create"] == 1
        listing = await client.get(
            '/api/v1/res.partner?domain=[["name","=","Simulada"]]&fields=name',
            headers=headers(),
        )
        assert listing.json()["rows"] == []

    async def test_dry_run_reports_validations(self, client: httpx.AsyncClient) -> None:
        resp = await client.post(
            "/api/v1/res.partner?dry_run=true",
            json={"values": {"ref": "falta nombre"}},
            headers=headers(),
        )
        assert resp.status_code == 201
        assert resp.json()["validations"][0]["code"] == "FIELD_REQUIRED"

    async def test_dry_run_needs_no_idempotency_key(self, client: httpx.AsyncClient) -> None:
        resp = await client.post(
            "/api/v1/res.partner?dry_run=true",
            json={"values": {"name": "X"}},
            headers=headers(),
        )
        assert resp.status_code == 201


class TestTransactionEndpoint:
    async def test_atomic_rolls_back_everything(self, client: httpx.AsyncClient) -> None:
        resp = await client.post(
            "/api/v1/tx",
            json={
                "atomic": True,
                "operations": [
                    {"op": "create", "model": "res.partner", "values": {"name": "Válida"}},
                    {"op": "create", "model": "res.partner", "values": {"ref": "falla"}},
                ],
            },
            headers=headers(uuid.uuid4().hex),
        )
        assert resp.status_code == 422
        listing = await client.get(
            '/api/v1/res.partner?domain=[["name","=","Válida"]]&fields=name', headers=headers()
        )
        assert listing.json()["rows"] == []

    async def test_non_atomic_partial_report(self, client: httpx.AsyncClient) -> None:
        resp = await client.post(
            "/api/v1/tx",
            json={
                "atomic": False,
                "operations": [
                    {"op": "create", "model": "res.partner", "values": {"name": "Ok1"}},
                    {"op": "create", "model": "res.partner", "values": {"ref": "falla"}},
                    {"op": "create", "model": "res.partner", "values": {"name": "Ok2"}},
                ],
            },
            headers=headers(uuid.uuid4().hex),
        )
        assert resp.status_code == 200, resp.text
        results = resp.json()["results"]
        assert [r["ok"] for r in results] == [True, False, True]
        assert results[1]["error"]["code"] == "FIELD_REQUIRED"
