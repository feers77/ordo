"""Agregaciones por HTTP (F2-08): agrupar y sumar sin traerse los registros."""

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
TENANT_PREFIX = "agg"
IVA = Decimal("1.19")


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
    """App propia con los routers necesarios y sin enforcement."""
    from ordo_api.actions import router as actions_router
    from ordo_api.deps import get_env, get_registry, get_session
    from ordo_api.records import router as records_router
    from ordo_runtime import create_app

    env = shop["env"]
    app = create_app("api")
    app.include_router(actions_router)
    app.include_router(records_router)

    async def override_session() -> AsyncIterator[AsyncSession]:
        yield env.session

    app.dependency_overrides[get_session] = override_session
    app.dependency_overrides[get_registry] = lambda: env.registry
    app.dependency_overrides[get_env] = lambda: env
    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


def headers(shop: dict[str, Any], key: str | None = None) -> dict[str, str]:
    out = {"X-Ordo-Tenant": shop["tenant"]}
    if key:
        out["Idempotency-Key"] = key
    return out


async def make_order(shop: dict[str, Any], partner_id: int, price: str) -> int:
    from modules.sale.services import SaleService

    return await SaleService(shop["env"]).create_order(
        partner_id=partner_id,
        date_order="2026-08-04",
        currency_id=shop["currency_id"],
        journal_id=shop["sale_journal"],
        company_id=shop["company_id"],
        lines=[
            {
                "name": "Licencia anual",
                "quantity": "1",
                "price_unit": Decimal(price),
                "tax_codes": "IVA19",
            }
        ],
    )


@pytest.fixture
async def orders(shop: dict[str, Any]) -> dict[str, Any]:
    """Tres órdenes, dos clientes y dos estados: el caso mínimo con contraste."""
    from modules.sale.services import SaleService

    service = SaleService(shop["env"])
    customer = shop["customer_id"]
    vendor = shop["vendor_id"]  # segundo partner, sirve igual de cliente aquí

    first = await make_order(shop, customer, "100000")
    second = await make_order(shop, customer, "50000")
    third = await make_order(shop, vendor, "200000")
    await service.action_confirm(first)
    await service.action_confirm(third)
    await shop["session"].commit()

    return {
        "customer": customer,
        "vendor": vendor,
        "draft_id": second,
        "customer_total": Decimal("100000") * IVA,
        "vendor_total": Decimal("200000") * IVA,
    }


async def aggregate(client: httpx.AsyncClient, shop: dict[str, Any], **body: Any) -> dict[str, Any]:
    response = await client.post("/api/v1/sale.order/aggregate", json=body, headers=headers(shop))
    assert response.status_code == 200, response.text
    payload: dict[str, Any] = response.json()
    return payload


class TestAggregate:
    async def test_totals_without_group_by(
        self, client: httpx.AsyncClient, shop: dict[str, Any], orders: dict[str, Any]
    ) -> None:
        payload = await aggregate(client, shop, domain=[], aggregates=["count", "sum:amount_total"])
        assert payload["total_groups"] == 1
        [group] = payload["groups"]
        assert group["count"] == 3
        expected = orders["customer_total"] + orders["vendor_total"]
        assert Decimal(group["sum:amount_total"]) == expected

    async def test_group_by_partner(
        self, client: httpx.AsyncClient, shop: dict[str, Any], orders: dict[str, Any]
    ) -> None:
        payload = await aggregate(
            client,
            shop,
            domain=[],
            group_by=["partner_id"],
            aggregates=["count", "sum:amount_total"],
        )
        assert payload["total_groups"] == 2
        by_partner = {group["partner_id"]: group for group in payload["groups"]}
        assert by_partner[orders["customer"]]["count"] == 2
        # La orden en borrador cuenta, pero todavía no aporta importe.
        customer_sum = Decimal(by_partner[orders["customer"]]["sum:amount_total"])
        assert customer_sum == orders["customer_total"]
        assert by_partner[orders["vendor"]]["count"] == 1
        assert Decimal(by_partner[orders["vendor"]]["sum:amount_total"]) == orders["vendor_total"]

    async def test_group_by_state_reflects_what_was_confirmed(
        self, client: httpx.AsyncClient, shop: dict[str, Any], orders: dict[str, Any]
    ) -> None:
        payload = await aggregate(
            client,
            shop,
            domain=[],
            group_by=["state"],
            aggregates=["count", "sum:amount_total"],
        )
        by_state = {group["state"]: group for group in payload["groups"]}
        assert by_state["confirmed"]["count"] == 2
        assert by_state["draft"]["count"] == 1
        # Sin importes fijados el SUM es NULL en SQL; la respuesta dice "0".
        assert by_state["draft"]["sum:amount_total"] == "0"

    async def test_order_by_aggregate_desc(
        self, client: httpx.AsyncClient, shop: dict[str, Any], orders: dict[str, Any]
    ) -> None:
        payload = await aggregate(
            client,
            shop,
            domain=[],
            group_by=["partner_id"],
            aggregates=["sum:amount_total"],
            order="sum:amount_total desc",
        )
        totals = [Decimal(group["sum:amount_total"]) for group in payload["groups"]]
        assert totals == sorted(totals, reverse=True)
        assert payload["groups"][0]["partner_id"] == orders["vendor"]

    async def test_domain_filters_the_set(
        self, client: httpx.AsyncClient, shop: dict[str, Any], orders: dict[str, Any]
    ) -> None:
        payload = await aggregate(
            client, shop, domain=[["state", "=", "draft"]], aggregates=["count"]
        )
        assert payload["groups"][0]["count"] == 1

    async def test_no_idempotency_key_required(
        self, client: httpx.AsyncClient, shop: dict[str, Any], orders: dict[str, Any]
    ) -> None:
        response = await client.post(
            "/api/v1/sale.order/aggregate",
            json={"aggregates": ["count"]},
            headers={"X-Ordo-Tenant": shop["tenant"]},
        )
        assert response.status_code == 200, response.text


class TestAggregateErrors:
    async def test_sum_over_text_field_is_422(
        self, client: httpx.AsyncClient, shop: dict[str, Any], orders: dict[str, Any]
    ) -> None:
        response = await client.post(
            "/api/v1/sale.order/aggregate",
            json={"aggregates": ["sum:name"]},
            headers=headers(shop),
        )
        assert response.status_code == 422
        assert response.json()["error"]["code"] == "AGGREGATE_INVALID_FIELD"

    async def test_unknown_group_field_is_422(
        self, client: httpx.AsyncClient, shop: dict[str, Any], orders: dict[str, Any]
    ) -> None:
        response = await client.post(
            "/api/v1/sale.order/aggregate",
            json={"group_by": ["no_existe"]},
            headers=headers(shop),
        )
        assert response.status_code == 422
        assert response.json()["error"]["code"] == "FIELD_UNKNOWN"
