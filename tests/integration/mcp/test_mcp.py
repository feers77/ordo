"""Un agente opera ORDO por MCP: descubrir, simular, vender, mirar los libros."""

import json
from typing import Any

import httpx
import pytest

pytestmark = pytest.mark.integration


async def rpc(
    client: httpx.AsyncClient,
    method: str,
    params: dict[str, Any] | None = None,
    *,
    tenant: str = "",
    request_id: int = 1,
) -> dict[str, Any]:
    headers = {"X-Ordo-Tenant": tenant} if tenant else {}
    response = await client.post(
        "/mcp",
        json={"jsonrpc": "2.0", "id": request_id, "method": method, "params": params or {}},
        headers=headers,
    )
    assert response.status_code == 200
    body: dict[str, Any] = response.json()
    return body


async def call_tool(
    client: httpx.AsyncClient, tenant: str, name: str, arguments: dict[str, Any]
) -> tuple[dict[str, Any], bool]:
    body = await rpc(
        client,
        "tools/call",
        {"name": name, "arguments": arguments},
        tenant=tenant,
    )
    result = body["result"]
    payload = json.loads(result["content"][0]["text"])
    return payload, result["isError"]


@pytest.fixture
async def client(shop: dict[str, Any]) -> Any:
    from ordo_mcp.main import app

    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


class TestProtocol:
    async def test_initialize_declares_tools_and_instructions(
        self, client: httpx.AsyncClient
    ) -> None:
        body = await rpc(client, "initialize", {"protocolVersion": "2025-03-26"})
        result = body["result"]
        assert result["serverInfo"]["name"] == "ordo"
        assert "tools" in result["capabilities"]
        assert "dry_run" in result["instructions"]

    async def test_tools_list_names_the_full_contract(self, client: httpx.AsyncClient) -> None:
        body = await rpc(client, "tools/list")
        names = {tool["name"] for tool in body["result"]["tools"]}
        assert {
            "ordo_schema",
            "ordo_search",
            "ordo_create",
            "ordo_run_action",
            "ordo_run_report",
        } <= names

    async def test_unknown_method_is_a_jsonrpc_error(self, client: httpx.AsyncClient) -> None:
        body = await rpc(client, "resources/list")
        assert body["error"]["code"] == -32601

    async def test_call_without_tenant_is_rejected(self, client: httpx.AsyncClient) -> None:
        body = await rpc(client, "tools/call", {"name": "ordo_schema", "arguments": {}})
        assert body["error"]["code"] == -32602

    async def test_notifications_are_acknowledged(self, client: httpx.AsyncClient) -> None:
        response = await client.post(
            "/mcp",
            json={"jsonrpc": "2.0", "method": "notifications/initialized"},
        )
        assert response.status_code == 202


class TestAgentFlow:
    async def test_full_sale_cycle_through_mcp(
        self, client: httpx.AsyncClient, shop: dict[str, Any]
    ) -> None:
        """Descubrir, crear, simular, confirmar, facturar y leer el balance."""
        tenant = shop["tenant"]

        schema, is_error = await call_tool(
            client, tenant, "ordo_schema", {"models": ["sale.order"]}
        )
        assert not is_error
        assert "sale.order" in json.dumps(schema)

        order_payload, is_error = await call_tool(
            client,
            tenant,
            "ordo_create",
            {
                "model": "sale.order",
                "values": {
                    "partner_id": shop["customer_id"],
                    "date_order": "2026-08-04",
                    "currency_id": shop["currency_id"],
                    "journal_id": shop["sale_journal"],
                    "company_id": shop["company_id"],
                },
            },
        )
        assert not is_error, order_payload
        order_id = order_payload["ids"][0]

        _, is_error = await call_tool(
            client,
            tenant,
            "ordo_create",
            {
                "model": "sale.order.line",
                "values": {
                    "order_id": order_id,
                    "name": "Licencia anual",
                    "quantity": "1",
                    "price_unit": "100000",
                    "tax_codes": "IVA19",
                    "company_id": shop["company_id"],
                },
            },
        )
        assert not is_error

        simulated, is_error = await call_tool(
            client,
            tenant,
            "ordo_run_action",
            {"model": "sale.order", "id": order_id, "action": "action_confirm", "dry_run": True},
        )
        assert not is_error
        assert simulated["would_return"]["name"] == "SO/00001"

        confirmed, is_error = await call_tool(
            client,
            tenant,
            "ordo_run_action",
            {"model": "sale.order", "id": order_id, "action": "action_confirm"},
        )
        assert not is_error
        assert confirmed["name"] == "SO/00001"  # el dry-run no quemó el número

        invoiced, is_error = await call_tool(
            client,
            tenant,
            "ordo_run_action",
            {"model": "sale.order", "id": order_id, "action": "action_invoice"},
        )
        assert not is_error
        assert invoiced["move_id"] > 0

        balance, is_error = await call_tool(
            client,
            tenant,
            "ordo_run_report",
            {"name": "account.trial_balance", "params": {"company_id": shop["company_id"]}},
        )
        assert not is_error
        assert balance["balanced"] is True

    async def test_actions_expose_approval_metadata(
        self, client: httpx.AsyncClient, shop: dict[str, Any]
    ) -> None:
        payload, is_error = await call_tool(
            client, shop["tenant"], "ordo_list_actions", {"model": "sale.order"}
        )
        assert not is_error
        by_name = {a["name"]: a for a in payload["actions"]}
        assert by_name["action_invoice"]["requires_approval"] is True

    async def test_domain_errors_come_back_with_stable_codes(
        self, client: httpx.AsyncClient, shop: dict[str, Any]
    ) -> None:
        payload, is_error = await call_tool(
            client,
            shop["tenant"],
            "ordo_run_action",
            {"model": "sale.order", "id": 999999, "action": "action_confirm"},
        )
        assert is_error
        assert payload["error"]["code"] == "SALE_ORDER_NOT_FOUND"

    async def test_unknown_tool_lists_the_alternatives(
        self, client: httpx.AsyncClient, shop: dict[str, Any]
    ) -> None:
        payload, is_error = await call_tool(client, shop["tenant"], "ordo_teleport", {})
        assert is_error
        assert payload["error"]["code"] == "TOOL_UNKNOWN"
        assert "ordo_search" in payload["error"]["hint"]

    async def test_search_reads_committed_data(
        self, client: httpx.AsyncClient, shop: dict[str, Any]
    ) -> None:
        payload, is_error = await call_tool(
            client,
            shop["tenant"],
            "ordo_search",
            {
                "model": "res.partner",
                "domain": [["name", "=", "Cliente Ltda"]],
                "fields": ["name", "vat"],
            },
        )
        assert not is_error
        assert payload["rows"][0]["name"] == "Cliente Ltda"
