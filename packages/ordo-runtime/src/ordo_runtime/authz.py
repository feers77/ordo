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
            raise ApprovalRequiredError(
                f"'{model}.{operation}' exige aprobación humana.",
                model=model,
                hint=(
                    "Crea la solicitud en POST /iam/v1/approvals y reintenta cuando esté aprobada."
                ),
            )
        return decision

    async def aclose(self) -> None:
        await self._client.aclose()


def check_tenant_header(decision_tenant: str, header_tenant: str | None) -> str:
    """El token manda; una cabecera que lo contradiga es un intento, no un typo."""
    if header_tenant and decision_tenant and header_tenant != decision_tenant:
        raise TenantMismatchError(
            "La cabecera X-Ordo-Tenant no coincide con el tenant del token.",
            hint="Quita la cabecera o usa la del tenant autenticado.",
        )
    return decision_tenant or (header_tenant or "")
