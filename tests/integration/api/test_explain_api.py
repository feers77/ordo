"""Explicación de un registro y catálogo global de acciones por HTTP.

El explain es la respuesta a "¿de dónde salió esto y qué puedo hacer?": se
prueba que responde con procedencia y motivos, y sobre todo que no escribe
nada, ni siquiera un número de secuencia.
"""

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
TENANT_PREFIX = "exp"


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
    from ordo_api.actions import router as actions_router
    from ordo_api.deps import get_env, get_registry, get_session
    from ordo_api.explain import router as explain_router
    from ordo_api.meta import router as meta_router
    from ordo_api.records import router as records_router
    from ordo_runtime import create_app

    env = shop["env"]
    # App propia, sin enforcement: aquí se prueba el contrato del explain,
    # no el PDP, que tiene su propia suite.
    app = create_app("api-explain-test")
    app.include_router(actions_router)
    app.include_router(explain_router)
    app.include_router(records_router)
    app.include_router(meta_router)

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


async def confirm(client: httpx.AsyncClient, shop: dict[str, Any], order_id: int) -> Any:
    return await client.post(
        f"/api/v1/sale.order/{order_id}/actions/action_confirm",
        json={"params": {}},
        headers=headers(shop, uuid.uuid4().hex),
    )


async def explain(client: httpx.AsyncClient, shop: dict[str, Any], order_id: int) -> Any:
    return await client.get(f"/api/v1/sale.order/{order_id}/explain", headers=headers(shop))


class TestExplain:
    async def test_draft_order_explains_values_and_actions(
        self, client: httpx.AsyncClient, shop: dict[str, Any]
    ) -> None:
        order_id = await make_order(shop)
        response = await explain(client, shop, order_id)
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["model"] == "sale.order"
        assert body["id"] == order_id

        state = body["fields"]["state"]
        assert state["value"] == "draft"
        assert state["origin"] in {"default", "stored"}
        assert state["agent_hint"]
        assert state["compute"] is None
        assert body["fields"]["partner_id"]["value"] == shop["customer_id"]
        assert isinstance(body["history"], list)

        available = {action["name"] for action in body["actions"]["available"]}
        blocked = {action["name"]: action for action in body["actions"]["blocked"]}
        assert "action_confirm" in available
        assert blocked["action_invoice"]["reason"]["code"] == "SALE_INVALID_TRANSITION"
        assert blocked["action_invoice"]["requires_approval"] is True
        assert blocked["action_invoice"]["reason"]["message"]

    async def test_confirming_moves_invoice_to_available(
        self, client: httpx.AsyncClient, shop: dict[str, Any]
    ) -> None:
        """El explain refleja el estado real: lo bloqueado se desbloquea solo."""
        order_id = await make_order(shop)
        before = (await explain(client, shop, order_id)).json()
        assert "action_invoice" not in {a["name"] for a in before["actions"]["available"]}

        assert (await confirm(client, shop, order_id)).status_code == 200

        after = (await explain(client, shop, order_id)).json()
        assert "action_invoice" in {a["name"] for a in after["actions"]["available"]}
        assert after["fields"]["state"]["value"] == "confirmed"
        assert after["fields"]["state"]["origin"] == "stored"

    async def test_explaining_twice_writes_nothing(
        self, client: httpx.AsyncClient, shop: dict[str, Any]
    ) -> None:
        """Simular acciones no cambia el registro ni quema numeración legal."""
        order_id = await make_order(shop)
        for _ in range(2):
            assert (await explain(client, shop, order_id)).status_code == 200

        state = await client.get(
            f"/api/v1/sale.order/{order_id}?fields=state,name", headers=headers(shop)
        )
        assert state.json()["state"] == "draft"
        assert state.json()["name"] is None

        confirmed = await confirm(client, shop, order_id)
        assert confirmed.json()["result"]["name"] == "SO/00001"

    async def test_unknown_record_is_404(
        self, client: httpx.AsyncClient, shop: dict[str, Any]
    ) -> None:
        response = await client.get("/api/v1/sale.order/999999/explain", headers=headers(shop))
        assert response.status_code == 404
        assert response.json()["error"]["code"] == "RECORD_NOT_FOUND"

    async def test_unknown_model_is_404(
        self, client: httpx.AsyncClient, shop: dict[str, Any]
    ) -> None:
        response = await client.get("/api/v1/no.such/1/explain", headers=headers(shop))
        assert response.status_code == 404
        assert response.json()["error"]["code"] == "MODEL_NOT_FOUND"


class TestActionCatalog:
    async def test_catalog_lists_actions_of_every_model_and_the_reports(
        self, client: httpx.AsyncClient, shop: dict[str, Any]
    ) -> None:
        response = await client.get("/meta/v1/actions", headers=headers(shop))
        assert response.status_code == 200, response.text
        body = response.json()

        models = {entry["model"] for entry in body["actions"]}
        assert {"sale.order", "purchase.order", "account.move"} <= models
        entries = {(entry["model"], entry["name"]): entry for entry in body["actions"]}
        assert entries[("sale.order", "action_invoice")]["requires_approval"] is True
        assert "reason" in entries[("sale.order", "action_credit_note")]["params"]

        reports = {report["name"] for report in body["reports"]}
        assert {"account.trial_balance", "stock.on_hand"} <= reports

    async def test_catalog_filters_by_model(
        self, client: httpx.AsyncClient, shop: dict[str, Any]
    ) -> None:
        response = await client.get("/meta/v1/actions?models=sale.order", headers=headers(shop))
        assert response.status_code == 200
        body = response.json()
        assert {entry["model"] for entry in body["actions"]} == {"sale.order"}
        names = [entry["name"] for entry in body["actions"]]
        assert names == sorted(names)
        assert {"action_confirm", "action_invoice", "action_einvoice"} <= set(names)
        assert body["reports"]

    async def test_catalog_rejects_an_unknown_model(
        self, client: httpx.AsyncClient, shop: dict[str, Any]
    ) -> None:
        response = await client.get("/meta/v1/actions?models=no.such", headers=headers(shop))
        assert response.status_code == 404
        assert response.json()["error"]["code"] == "MODEL_NOT_FOUND"
