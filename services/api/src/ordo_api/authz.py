"""Token enforcement for the generic API (ADR-016).

The middleware maps each data route to (model, operation), forwards the
bearer to the central PDP and binds the tenant that IAM resolved from the
token. With `ORDO_IAM_URL` unset the API runs open for internal networks —
and logs it loudly at startup.
"""

from __future__ import annotations

import json
from typing import Any

from fastapi import FastAPI, Request, Response
from fastapi.responses import JSONResponse
from ordo_runtime import OrdoError
from ordo_runtime.authz import (
    ApprovalRequiredError,
    PDPClient,
    check_tenant_header,
    enforcement_enabled,
    sealed_operation,
    warn_if_open,
)

OPEN_PATHS = {"/healthz", "/docs", "/openapi.json", "/redoc"}
METHOD_OPS = {"GET": "read", "POST": "create", "PATCH": "write", "DELETE": "unlink"}
# Los reportes no son un modelo del registry: se autorizan contra el
# pseudo-modelo "reports" declarado en security.yaml.
REPORTS_MODEL = "reports"


def route_to_authz(method: str, path: str) -> tuple[str, str, int | None] | None:
    """(modelo, operación, record_id) de una ruta de datos; None si es abierta."""
    if path in OPEN_PATHS or not path.startswith(("/api/", "/meta/")):
        return None
    if path.startswith("/meta/"):
        # Introspección: leer el schema es leer metadatos del sistema.
        return ("ir.model", "read", None)
    parts = [p for p in path.split("/") if p][2:]  # sin "api", "v1"
    if not parts:
        return None
    if parts[0] == "reports":
        return (REPORTS_MODEL, "read", None)
    if parts[0] == "tx":
        # La transacción multi-operación se autoriza por cada operación
        # interna en el runner; aquí exige al menos identidad válida.
        return ("ir.model", "read", None)
    model = parts[0]
    record_id = int(parts[1]) if len(parts) >= 2 and parts[1].isdigit() else None
    if len(parts) >= 4 and parts[2] == "actions":
        return (model, parts[3], record_id)
    if len(parts) >= 2 and parts[1] == "actions":
        return (model, "read", None)  # descubrimiento de acciones
    if len(parts) >= 2 and parts[1] == "aggregate":
        # Agregar es leer: el POST lleva el dominio en el cuerpo, no escribe nada.
        return (model, "read", None)
    if len(parts) >= 2 and parts[1] == "batch":
        return (model, "write", None)
    return (model, METHOD_OPS.get(method, "write"), record_id)


def install_enforcement(app: FastAPI, client: PDPClient | None = None) -> None:
    warn_if_open("ordo-api")
    if not enforcement_enabled() and client is None:
        return

    pdp = client or PDPClient()

    @app.middleware("http")
    async def enforce(request: Request, call_next: Any) -> Response:
        target = route_to_authz(request.method, request.url.path)
        if target is None:
            return await call_next(request)  # type: ignore[no-any-return]
        model, operation, record_id = target
        authorization = request.headers.get("Authorization", "")
        bearer = authorization.removeprefix("Bearer ").strip() or None
        try:
            try:
                decision_tenant = (
                    await pdp.authorize(bearer=bearer, model=model, operation=operation)
                ).tenant
            except ApprovalRequiredError as pending:
                approval_id = request.headers.get("X-Ordo-Approval")
                if not approval_id or bearer is None:
                    raise
                # Consumir la aprobación sellada ejecuta la operación una sola
                # vez: el cuerpo debe coincidir byte a byte con lo aprobado.
                raw = await request.body()
                body = json.loads(raw) if raw else {}
                await pdp.consume_approval(
                    bearer=bearer,
                    approval_id=approval_id,
                    operation=sealed_operation(model, operation, record_id, body),
                )

                async def replay() -> dict[str, Any]:
                    return {"type": "http.request", "body": raw, "more_body": False}

                request._receive = replay  # el downstream vuelve a leer el cuerpo
                decision_tenant = getattr(pending, "decision_tenant", "")
            tenant = check_tenant_header(decision_tenant, request.headers.get("X-Ordo-Tenant"))
        except OrdoError as exc:
            return JSONResponse(exc.to_payload(), status_code=exc.status_code)
        request.state.authz_tenant = tenant
        return await call_next(request)  # type: ignore[no-any-return]
