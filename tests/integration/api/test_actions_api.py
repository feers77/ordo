"""Acciones de negocio por HTTP: descubrimiento, dry-run, idempotencia y flujo real."""

import uuid
from collections.abc import AsyncIterator
from decimal import Decimal
from pathlib import Path
from typing import Any

import httpx
import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from tests.integration.commercial import build_shop

pytestmark = pytest.mark.integration

MODULES_ROOT = Path(__file__).resolve().parents[3] / "modules"
TENANT_PREFIX = "act"


@pytest.fixture
async def shop(core_db_url: str) -> AsyncIterator[dict[str, Any]]:
    tenant = f"{TENANT_PREFIX}{uuid.uuid4().hex[:8]}"
    engine = create_async_engine(core_db_url)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    session: AsyncSession = maker()
    data = await build_shop(session, tenant, modules_root=MODULES_ROOT)
    data["tenant"] = tenant
    yield data
    await session.close()
    await engine.dispose()


@pytest.fixture
async def client(shop: dict[str, Any]) -> AsyncIterator[httpx.AsyncClient]:
    from ordo_api.deps import get_env, get_registry, get_session
    from ordo_api.main import app

    env = shop["env"]

    async def override_session() -> AsyncIterator[AsyncSession]:
        yield env.session

    app.dependency_overrides[get_session] = override_session
    app.dependency_overrides[get_registry] = lambda: env.registry
    app.dependency_overrides[get_env] = lambda: env
    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
    try:
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
            yield c
    finally:
        app.dependency_overrides.clear()


def headers(shop: dict[str, Any], key: str | None = None) -> dict[str, str]:
    out = {"X-Ordo-Tenant": shop["tenant"]}
    if key:
        out["Idempotency-Key"] = key
    return out


async def make_order(shop: dict[str, Any]) -> int:
    from modules.sale.services import SaleService

    return await SaleService(shop["env"]).create_order(
        partner_id=shop["customer_id"],
        date_order="2026-08-04",
        currency_id=shop["currency_id"],
        journal_id=shop["sale_journal"],
        company_id=shop["company_id"],
        lines=[
            {
                "name": "Licencia anual",
                "quantity": "1",
                "price_unit": Decimal("100000"),
                "tax_codes": "IVA19",
            }
        ],
    )


class TestDiscovery:
    async def test_actions_are_discoverable_with_approval_metadata(
        self, client: httpx.AsyncClient, shop: dict[str, Any]
    ) -> None:
        response = await client.get("/api/v1/sale.order/actions", headers=headers(shop))
        assert response.status_code == 200
        actions = {a["name"]: a for a in response.json()["actions"]}
        assert set(actions) >= {
            "action_confirm",
            "action_invoice",
            "action_cancel",
            "action_einvoice",
        }
        assert actions["action_invoice"]["requires_approval"] is True
        assert actions["action_confirm"]["requires_approval"] is False

    async def test_unknown_model_is_404(
        self, client: httpx.AsyncClient, shop: dict[str, Any]
    ) -> None:
        response = await client.get("/api/v1/no.such/actions", headers=headers(shop))
        assert response.status_code == 404

    async def test_unknown_action_is_404_with_hint(
        self, client: httpx.AsyncClient, shop: dict[str, Any]
    ) -> None:
        response = await client.post(
            "/api/v1/sale.order/1/actions/action_teleport",
            json={"params": {}},
            headers=headers(shop, uuid.uuid4().hex),
        )
        assert response.status_code == 404
        error = response.json()["error"]
        assert error["code"] == "ACTION_UNKNOWN"
        assert "action_confirm" in error["hint"]


class TestContract:
    async def test_write_without_idempotency_key_is_rejected(
        self, client: httpx.AsyncClient, shop: dict[str, Any]
    ) -> None:
        order_id = await make_order(shop)
        response = await client.post(
            f"/api/v1/sale.order/{order_id}/actions/action_confirm",
            json={"params": {}},
            headers=headers(shop),
        )
        assert response.status_code == 400
        assert response.json()["error"]["code"] == "IDEMPOTENCY_KEY_REQUIRED"

    async def test_dry_run_simulates_without_burning_the_sequence(
        self, client: httpx.AsyncClient, shop: dict[str, Any]
    ) -> None:
        """La simulación devuelve el resultado y lo deshace todo, número incluido."""
        order_id = await make_order(shop)
        simulated = await client.post(
            f"/api/v1/sale.order/{order_id}/actions/action_confirm?dry_run=true",
            json={"params": {}},
            headers=headers(shop),
        )
        assert simulated.status_code == 200
        assert simulated.json()["result"]["would_return"]["name"] == "SO/00001"

        state = await client.get(
            f"/api/v1/sale.order/{order_id}?fields=state", headers=headers(shop)
        )
        assert state.json()["state"] == "draft"

        real = await client.post(
            f"/api/v1/sale.order/{order_id}/actions/action_confirm",
            json={"params": {}},
            headers=headers(shop, uuid.uuid4().hex),
        )
        assert real.json()["result"]["name"] == "SO/00001"  # el dry-run no quemó número

    async def test_replaying_the_same_key_returns_the_same_response(
        self, client: httpx.AsyncClient, shop: dict[str, Any]
    ) -> None:
        order_id = await make_order(shop)
        key = uuid.uuid4().hex
        first = await client.post(
            f"/api/v1/sale.order/{order_id}/actions/action_confirm",
            json={"params": {}},
            headers=headers(shop, key),
        )
        replay = await client.post(
            f"/api/v1/sale.order/{order_id}/actions/action_confirm",
            json={"params": {}},
            headers=headers(shop, key),
        )
        assert first.status_code == replay.status_code == 200
        assert first.json() == replay.json()

    async def test_domain_errors_keep_the_stable_code(
        self, client: httpx.AsyncClient, shop: dict[str, Any]
    ) -> None:
        order_id = await make_order(shop)
        response = await client.post(
            f"/api/v1/sale.order/{order_id}/actions/action_invoice",
            json={"params": {}},
            headers=headers(shop, uuid.uuid4().hex),
        )
        assert response.status_code == 422
        assert response.json()["error"]["code"] == "SALE_INVALID_TRANSITION"


class TestBusinessFlow:
    async def test_confirm_invoice_and_the_move_is_posted(
        self, client: httpx.AsyncClient, shop: dict[str, Any]
    ) -> None:
        order_id = await make_order(shop)
        await client.post(
            f"/api/v1/sale.order/{order_id}/actions/action_confirm",
            json={"params": {}},
            headers=headers(shop, uuid.uuid4().hex),
        )
        invoiced = await client.post(
            f"/api/v1/sale.order/{order_id}/actions/action_invoice",
            json={"params": {}},
            headers=headers(shop, uuid.uuid4().hex),
        )
        assert invoiced.status_code == 200
        assert invoiced.json()["requires_approval"] is True
        move_id = invoiced.json()["result"]["move_id"]

        move = await client.get(
            f"/api/v1/account.move/{move_id}?fields=state,name", headers=headers(shop)
        )
        assert move.json()["state"] == "posted"
        assert move.json()["name"] == "VTA/2026/00001"

    async def test_action_emits_an_outbox_event(
        self, client: httpx.AsyncClient, shop: dict[str, Any]
    ) -> None:
        from sqlalchemy import text as sql_text

        order_id = await make_order(shop)
        await client.post(
            f"/api/v1/sale.order/{order_id}/actions/action_confirm",
            json={"params": {}},
            headers=headers(shop, uuid.uuid4().hex),
        )
        rows = (
            await shop["session"].execute(
                sql_text(
                    "SELECT event_type, subject FROM ir_outbox "
                    "WHERE event_type = 'sale.order.action_confirm'"
                )
            )
        ).all()
        assert rows
        assert rows[0].subject == f"sale.order/{order_id}"

    async def test_einvoice_from_the_confirmed_order(
        self, client: httpx.AsyncClient, shop: dict[str, Any]
    ) -> None:
        from modules.einvoicing.tests.test_sii import make_caf, make_key
        from ordo_core.recordset import RecordSet

        order_id = await make_order(shop)
        await client.post(
            f"/api/v1/sale.order/{order_id}/actions/action_confirm",
            json={"params": {}},
            headers=headers(shop, uuid.uuid4().hex),
        )
        ranges = RecordSet(shop["env"], "edi.folio.range")
        await ranges.create(
            [
                {
                    "country_code": "cl",
                    "document_type_code": "33",
                    "range_from": 1,
                    "range_to": 100,
                    "next_number": 1,
                    "authorization_code": make_caf(make_key()),
                    "company_id": shop["company_id"],
                }
            ]
        )
        response = await client.post(
            f"/api/v1/sale.order/{order_id}/actions/action_einvoice",
            json={"params": {"document_type_code": "33"}},
            headers=headers(shop, uuid.uuid4().hex),
        )
        assert response.status_code == 200, response.text
        result = response.json()["result"]
        assert result["number"] == 1
        assert result["state"] == "generated"

        document = await client.get(
            f"/api/v1/edi.document/{result['document_id']}"
            "?fields=state,xml_payload,payload_encoding",
            headers=headers(shop),
        )
        assert document.json()["state"] == "generated"
        assert "<TED" in document.json()["xml_payload"]
        assert document.json()["payload_encoding"] == "iso-8859-1"
