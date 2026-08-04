"""Token enforcement for the generic API (ADR-016).

The middleware maps each data route to (model, operation), forwards the
bearer to the central PDP and binds the tenant that IAM resolved from the
token. With `ORDO_IAM_URL` unset the API runs open for internal networks —
and logs it loudly at startup.
"""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI, Request, Response
from fastapi.responses import JSONResponse
from ordo_runtime import OrdoError
from ordo_runtime.authz import (
    PDPClient,
    check_tenant_header,
    enforcement_enabled,
    warn_if_open,
)

OPEN_PATHS = {"/healthz", "/docs", "/openapi.json", "/redoc"}
METHOD_OPS = {"GET": "read", "POST": "create", "PATCH": "write", "DELETE": "unlink"}
# Los reportes no son un modelo del registry: se autorizan contra el
# pseudo-modelo "reports" declarado en security.yaml.
REPORTS_MODEL = "reports"


def route_to_authz(method: str, path: str) -> tuple[str, str] | None:
    """(modelo, operación) de una ruta de datos; None si la ruta es abierta."""
    if path in OPEN_PATHS or not path.startswith(("/api/", "/meta/")):
        return None
    if path.startswith("/meta/"):
        # Introspección: leer el schema es leer metadatos del sistema.
        return ("ir.model", "read")
    parts = [p for p in path.split("/") if p][2:]  # sin "api", "v1"
    if not parts:
        return None
    if parts[0] == "reports":
        return (REPORTS_MODEL, "read")
    if parts[0] == "tx":
        # La transacción multi-operación se autoriza por cada operación
        # interna en el runner; aquí exige al menos identidad válida.
        return ("ir.model", "read")
    model = parts[0]
    if len(parts) >= 4 and parts[2] == "actions":
        return (model, parts[3])
    if len(parts) >= 2 and parts[1] == "actions":
        return (model, "read")  # descubrimiento de acciones
    if len(parts) >= 2 and parts[1] == "batch":
        return (model, "write")
    return (model, METHOD_OPS.get(method, "write"))


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
        model, operation = target
        authorization = request.headers.get("Authorization", "")
        bearer = authorization.removeprefix("Bearer ").strip() or None
        try:
            decision = await pdp.authorize(bearer=bearer, model=model, operation=operation)
            tenant = check_tenant_header(decision.tenant, request.headers.get("X-Ordo-Tenant"))
        except OrdoError as exc:
            return JSONResponse(exc.to_payload(), status_code=exc.status_code)
        request.state.authz_tenant = tenant
        return await call_next(request)  # type: ignore[no-any-return]
