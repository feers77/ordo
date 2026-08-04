"""Servicio ordo-mcp: ORDO operable por agentes vía MCP (F3-01, ADR-015).

Transporte "streamable HTTP": JSON-RPC 2.0 sobre `POST /mcp`. Solo tools;
el resto del protocolo se agrega cuando haga falta, no antes.
"""

from __future__ import annotations

import json
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from fastapi import Request, Response
from ordo_core.errors import KernelError
from ordo_runtime import OrdoError, create_app
from ordo_runtime.authz import (
    PDPClient,
    check_tenant_header,
    enforcement_enabled,
    warn_if_open,
)

from ordo_mcp.deps import build_env, session_maker
from ordo_mcp.tools import HANDLERS, TOOLS, tool_authz_target

PROTOCOL_VERSION = "2025-03-26"
PARSE_ERROR = -32700
INVALID_REQUEST = -32600
METHOD_NOT_FOUND = -32601
INVALID_PARAMS = -32602

app = create_app("mcp")
warn_if_open("ordo-mcp")
_pdp: PDPClient | None = PDPClient() if enforcement_enabled() else None


def set_pdp_client(client: PDPClient | None) -> None:
    """Inyección para tests; en producción se construye desde ORDO_IAM_URL."""
    global _pdp
    _pdp = client


def _json_default(value: Any) -> str:
    if isinstance(value, Decimal | datetime | date):
        return str(value)
    raise TypeError(f"No serializable: {type(value).__name__}")


def _result(request_id: Any, result: dict[str, Any]) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": request_id, "result": result}


def _error(request_id: Any, code: int, message: str) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": request_id, "error": {"code": code, "message": message}}


def _tool_text(payload: dict[str, Any], *, is_error: bool = False) -> dict[str, Any]:
    return {
        "content": [{"type": "text", "text": json.dumps(payload, default=_json_default, indent=2)}],
        "isError": is_error,
    }


async def _call_tool(name: str, arguments: dict[str, Any], tenant: str) -> dict[str, Any]:
    handler = HANDLERS.get(name)
    if handler is None:
        available = ", ".join(sorted(HANDLERS))
        return _tool_text(
            {
                "error": {
                    "code": "TOOL_UNKNOWN",
                    "message": f"No existe la tool '{name}'",
                    "hint": f"Tools disponibles: {available}.",
                }
            },
            is_error=True,
        )
    maker = session_maker()
    async with maker() as session:
        env = await build_env(session, tenant)
        try:
            result = await handler(env, arguments)
        except KernelError as exc:
            await session.rollback()
            return _tool_text(
                {"error": {"code": exc.code, "message": exc.message, "hint": exc.hint}},
                is_error=True,
            )
        await session.commit()
    return _tool_text(result)


@app.post("/mcp")
async def mcp_endpoint(request: Request) -> Response:
    try:
        message = json.loads(await request.body())
    except json.JSONDecodeError:
        return Response(
            json.dumps(_error(None, PARSE_ERROR, "JSON inválido")),
            media_type="application/json",
        )
    if not isinstance(message, dict) or message.get("jsonrpc") != "2.0":
        return Response(
            json.dumps(_error(None, INVALID_REQUEST, "Se espera JSON-RPC 2.0")),
            media_type="application/json",
        )

    method = message.get("method", "")
    request_id = message.get("id")
    params = message.get("params") or {}

    if request_id is None:
        # Notificación (p. ej. notifications/initialized): se acusa y listo.
        return Response(status_code=202)

    if method == "initialize":
        payload = _result(
            request_id,
            {
                "protocolVersion": PROTOCOL_VERSION,
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "ordo", "version": "0.1.0"},
                "instructions": (
                    "ORDO es un ERP operado por agentes. Empieza con ordo_schema para "
                    "descubrir los modelos, usa dry_run antes de escribir, y revisa "
                    "requires_approval en ordo_list_actions: esas operaciones exigen "
                    "aprobación humana. Importes siempre como string decimal."
                ),
            },
        )
    elif method == "ping":
        payload = _result(request_id, {})
    elif method == "tools/list":
        payload = _result(request_id, {"tools": TOOLS})
    elif method == "tools/call":
        name = str(params.get("name", ""))
        arguments = params.get("arguments") or {}
        header_tenant = request.headers.get("X-Ordo-Tenant", "")
        if _pdp is not None:
            authorization = request.headers.get("Authorization", "")
            bearer = authorization.removeprefix("Bearer ").strip() or None
            model, operation = tool_authz_target(name, arguments)
            try:
                decision = await _pdp.authorize(bearer=bearer, model=model, operation=operation)
                tenant = check_tenant_header(decision.tenant, header_tenant or None)
            except OrdoError as exc:
                payload = _result(
                    request_id,
                    _tool_text({"error": exc.to_payload()["error"]}, is_error=True),
                )
                return Response(
                    json.dumps(payload, default=_json_default),
                    media_type="application/json",
                )
        else:
            tenant = header_tenant
        if not tenant:
            payload = _error(request_id, INVALID_PARAMS, "Falta la cabecera X-Ordo-Tenant")
        else:
            result = await _call_tool(name, arguments, tenant)
            payload = _result(request_id, result)
    else:
        payload = _error(request_id, METHOD_NOT_FOUND, f"Método desconocido: {method}")

    return Response(json.dumps(payload, default=_json_default), media_type="application/json")
