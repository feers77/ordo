"""Mapeo ruta/tool → (modelo, operación) que evalúa el PDP (ADR-016)."""

from ordo_api.authz import route_to_authz
from ordo_mcp.tools import tool_authz_target


class TestApiRoutes:
    def test_open_paths_skip_enforcement(self) -> None:
        assert route_to_authz("GET", "/healthz") is None
        assert route_to_authz("GET", "/docs") is None

    def test_crud_maps_to_model_and_method(self) -> None:
        assert route_to_authz("GET", "/api/v1/sale.order") == ("sale.order", "read")
        assert route_to_authz("POST", "/api/v1/sale.order") == ("sale.order", "create")
        assert route_to_authz("PATCH", "/api/v1/sale.order/5") == ("sale.order", "write")
        assert route_to_authz("DELETE", "/api/v1/sale.order/5") == ("sale.order", "unlink")

    def test_actions_carry_their_name(self) -> None:
        assert route_to_authz("POST", "/api/v1/sale.order/5/actions/action_invoice") == (
            "sale.order",
            "action_invoice",
        )
        assert route_to_authz("GET", "/api/v1/sale.order/actions") == ("sale.order", "read")

    def test_reports_and_meta_use_pseudo_models(self) -> None:
        assert route_to_authz("GET", "/api/v1/reports/account.trial_balance") == (
            "reports",
            "read",
        )
        assert route_to_authz("GET", "/meta/v1/schema") == ("ir.model", "read")


class TestMcpTools:
    def test_read_tools(self) -> None:
        assert tool_authz_target("ordo_search", {"model": "sale.order"}) == (
            "sale.order",
            "read",
        )
        assert tool_authz_target("ordo_schema", {}) == ("ir.model", "read")

    def test_writes_and_actions(self) -> None:
        assert tool_authz_target("ordo_create", {"model": "sale.order"}) == (
            "sale.order",
            "create",
        )
        assert tool_authz_target(
            "ordo_run_action", {"model": "account.move", "action": "action_post"}
        ) == ("account.move", "action_post")

    def test_reports_pseudo_model(self) -> None:
        assert tool_authz_target("ordo_run_report", {"name": "x"}) == ("reports", "read")
