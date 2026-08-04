"""PDP client: services forward the bearer and act on IAM's decision (ADR-016).

The service never verifies signatures nor interprets caps: IAM is the only
authority. Fail-closed by design — an unreachable PDP denies, never allows.
With `ORDO_IAM_URL` unset the service runs open (internal network only) and
says so loudly at startup.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from typing import Any

import httpx

from ordo_runtime.errors import OrdoError

logger = logging.getLogger("ordo.runtime.authz")


class AuthRequiredError(OrdoError):
    code = "AUTH_REQUIRED"
    status_code = 401


class AuthDeniedError(OrdoError):
    code = "AUTH_DENIED"
    status_code = 403


class ApprovalRequiredError(OrdoError):
    code = "IAM_APPROVAL_REQUIRED"
    status_code = 403
    requires_approval = True
    # Tenant resuelto por el PDP: lo usa quien consume la aprobación y sigue.
    decision_tenant: str = ""


class PdpUnavailableError(OrdoError):
    code = "AUTH_PDP_UNAVAILABLE"
    status_code = 503
    retryable = True


class TenantMismatchError(OrdoError):
    code = "AUTH_TENANT_MISMATCH"
    status_code = 403


@dataclass(frozen=True)
class AuthzDecision:
    allowed: bool
    reason: str
    requires_approval: bool
    tenant: str


def iam_url() -> str | None:
    return os.environ.get("ORDO_IAM_URL") or None


def enforcement_enabled() -> bool:
    return iam_url() is not None


def warn_if_open(service: str) -> None:
    if not enforcement_enabled():
        logger.warning(
            "%s SIN enforcement de tokens (ORDO_IAM_URL vacía): solo apto para red interna",
            service,
        )


class PDPClient:
    """Thin client for POST /iam/v1/authorize. Inject `client` in tests."""

    def __init__(self, client: httpx.AsyncClient | None = None) -> None:
        base = iam_url()
        if client is not None:
            self._client = client
        elif base is not None:
            self._client = httpx.AsyncClient(base_url=base, timeout=5.0)
        else:  # pragma: no cover - construcción sin enforcement
            msg = "PDPClient sin ORDO_IAM_URL ni cliente inyectado"
            raise RuntimeError(msg)

    async def authorize(
        self,
        *,
        bearer: str | None,
        model: str,
        operation: str,
        amount: dict[str, str] | None = None,
    ) -> AuthzDecision:
        if not bearer:
            raise AuthRequiredError(
                "Falta el header Authorization.",
                hint="Obtén un token en /iam/v1/token (agentes) o vía OIDC (personas).",
            )
        payload: dict[str, Any] = {"model": model, "operation": operation}
        if amount is not None:
            payload["amount"] = amount
        try:
            response = await self._client.post(
                "/iam/v1/authorize",
                json=payload,
                headers={"Authorization": f"Bearer {bearer}"},
            )
        except httpx.HTTPError as exc:
            raise PdpUnavailableError(
                "El PDP no responde; el request se rechaza (fail-closed).",
                hint="Revisa el servicio IAM y reintenta.",
            ) from exc
        if response.status_code == 401:
            raise AuthRequiredError(
                "Token inválido o vencido.",
                hint="Renueva el token; los de agente viven 15 minutos.",
            )
        if response.status_code >= 500:
            raise PdpUnavailableError(
                "El PDP falló al evaluar; el request se rechaza (fail-closed)."
            )
        body = response.json()
        decision = AuthzDecision(
            allowed=bool(body.get("allowed")),
            reason=str(body.get("reason", "")),
            requires_approval=bool(body.get("requires_approval")),
            tenant=str(body.get("tenant", "")),
        )
        if not decision.allowed:
            raise AuthDeniedError(
                f"Operación denegada: {decision.reason}",
                model=model,
                hint="Revisa el rol del usuario efectivo y el cap del agente.",
            )
        if decision.requires_approval:
            error = ApprovalRequiredError(
                f"'{model}.{operation}' exige aprobación humana.",
                model=model,
                hint=(
                    "Crea la solicitud en POST /iam/v1/approvals, espera la "
                    "resolución y reintenta con X-Ordo-Approval: <id>."
                ),
            )
            # El middleware necesita el tenant si va a consumir una aprobación
            # y seguir adelante sin re-autorizar.
            error.decision_tenant = decision.tenant
            raise error
        return decision

    async def consume_approval(
        self,
        *,
        bearer: str,
        approval_id: str,
        operation: dict[str, Any],
    ) -> None:
        """Consumes the sealed approval; IAM's stable error passes through."""
        try:
            response = await self._client.post(
                f"/iam/v1/approvals/{approval_id}/consume",
                json={"operation": operation},
                headers={"Authorization": f"Bearer {bearer}"},
            )
        except httpx.HTTPError as exc:
            raise PdpUnavailableError(
                "IAM no responde al consumir la aprobación; el request se rechaza."
            ) from exc
        if response.status_code < 300:
            return
        try:
            error = response.json().get("error", {})
        except ValueError:
            error = {}
        raise OrdoError(
            str(error.get("message", "No se pudo consumir la aprobación.")),
            code=str(error.get("code", "IAM_APPROVAL_INVALID")),
            status_code=response.status_code,
            hint=error.get("hint"),
        )

    async def aclose(self) -> None:
        await self._client.aclose()


def sealed_operation(
    model: str, operation: str, record_id: int | None, body: Any
) -> dict[str, Any]:
    """La operación tal como debe sellarse en la aprobación (contrato público).

    El agente crea la aprobación con EXACTAMENTE este objeto; consumirla con
    cualquier otra cosa es IAM_APPROVAL_MISMATCH, byte a byte.
    """
    return {
        "model": model,
        "operation": operation,
        "payload": {"record_id": record_id, "body": body if body is not None else {}},
    }


def check_tenant_header(decision_tenant: str, header_tenant: str | None) -> str:
    """El token manda; una cabecera que lo contradiga es un intento, no un typo."""
    if header_tenant and decision_tenant and header_tenant != decision_tenant:
        raise TenantMismatchError(
            "La cabecera X-Ordo-Tenant no coincide con el tenant del token.",
            hint="Quita la cabecera o usa la del tenant autenticado.",
        )
    return decision_tenant or (header_tenant or "")
