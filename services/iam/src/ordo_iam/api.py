"""HTTP API of ordo-iam (F1-02 /me, F1-03 agents + token exchange)."""

from __future__ import annotations

import asyncio
import os
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from functools import lru_cache
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, Form, Header, Response
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from ordo_iam.agent_auth import new_credentials, verify_secret
from ordo_iam.approvals import ApprovalService
from ordo_iam.audit import append_audit
from ordo_iam.bridge import IdentityBridge
from ordo_iam.captokens import merge_caps
from ordo_iam.db import get_session
from ordo_iam.errors import (
    AgentAuthFailedError,
    AgentSuspendedError,
    ApprovalNotFoundError,
    DelegationNotAllowedError,
    IdempotencyKeyRequiredError,
    NoCapabilitiesError,
    NotAgentOwnerError,
    TokenInvalidError,
    UnsupportedGrantError,
)
from ordo_iam.keys import public_jwks
from ordo_iam.models import AutonomyLevel, Principal, PrincipalStatus, User
from ordo_iam.oidc import OIDCVerifier
from ordo_iam.pdp import (
    AccessRequest,
    Amount,
    PolicyEngine,
    RedisUsageCounter,
    UsageCounter,
)
from ordo_iam.repository import PrincipalRepository
from ordo_iam.tokens import AGENT_TOKEN_TTL_S, issue_agent_token

router = APIRouter(prefix="/iam/v1", tags=["iam"])

bearer = HTTPBearer(auto_error=False)

TOKEN_EXCHANGE_GRANT = "urn:ietf:params:oauth:grant-type:token-exchange"  # noqa: S105
ACCESS_TOKEN_TYPE = "urn:ietf:params:oauth:token-type:access_token"  # noqa: S105


@lru_cache(maxsize=1)
def get_verifier() -> OIDCVerifier:
    issuer = os.environ.get("OIDC_ISSUER", "http://127.0.0.1:8080/realms/ordo")
    audience = os.environ.get("OIDC_AUDIENCE", "ordo-api")
    return OIDCVerifier(issuer=issuer, audience=audience, jwks_url=os.environ.get("OIDC_JWKS_URL"))


async def current_user(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)],
    session: Annotated[AsyncSession, Depends(get_session)],
    verifier: Annotated[OIDCVerifier, Depends(get_verifier)],
) -> User:
    if credentials is None:
        raise TokenInvalidError(
            "Falta el header Authorization.",
            hint="Envía 'Authorization: Bearer <access_token>'.",
        )
    claims = await asyncio.to_thread(verifier.verify, credentials.credentials)
    return await IdentityBridge(session).resolve(claims)


# ---------------------------------------------------------------- /me


class MeResponse(BaseModel):
    principal_id: UUID
    tenant: str
    type: str
    display_name: str
    email: str


@router.get("/me", response_model=MeResponse)
async def me(
    user: Annotated[User, Depends(current_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> MeResponse:
    principal = await session.get(Principal, user.principal_id)
    assert principal is not None
    return MeResponse(
        principal_id=user.principal_id,
        tenant=user.tenant,
        type=principal.type.value,
        display_name=principal.display_name,
        email=user.email,
    )


# ---------------------------------------------------------------- agents


class RegisterAgentRequest(BaseModel):
    display_name: str
    model: str
    model_version: str | None = None
    autonomy_level: AutonomyLevel = AutonomyLevel.observer
    budget: dict[str, Any] | None = None


class RegisterAgentResponse(BaseModel):
    agent_id: UUID
    agent_secret: str
    autonomy_level: AutonomyLevel


@router.post("/agents", response_model=RegisterAgentResponse, status_code=201)
async def register_agent(
    body: RegisterAgentRequest,
    user: Annotated[User, Depends(current_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> RegisterAgentResponse:
    secret, salt, digest = new_credentials()
    agent = await PrincipalRepository(session).create_agent(
        tenant=user.tenant,
        owner_user_id=user.principal_id,
        display_name=body.display_name,
        model=body.model,
        model_version=body.model_version,
        autonomy_level=body.autonomy_level,
        budget=body.budget,
        secret_hash=digest,
        secret_salt=salt,
    )
    return RegisterAgentResponse(
        agent_id=agent.principal_id,
        agent_secret=secret,
        autonomy_level=agent.autonomy_level,
    )


class GrantRequest(BaseModel):
    cap: dict[str, Any]
    valid_from: datetime | None = None
    valid_until: datetime | None = None


class GrantResponse(BaseModel):
    grant_id: UUID


@router.post("/agents/{agent_id}/grants", response_model=GrantResponse, status_code=201)
async def grant_capability(
    agent_id: UUID,
    body: GrantRequest,
    user: Annotated[User, Depends(current_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> GrantResponse:
    repo = PrincipalRepository(session)
    agent = await repo.get_agent(agent_id)
    if agent is None or agent.owner_user_id != user.principal_id:
        raise NotAgentOwnerError(
            "Solo el dueño del agente puede otorgar capacidades.",
            hint="Verifica el agent_id o pide al dueño que otorgue el grant.",
        )
    grant = await repo.grant_capability(
        agent_id=agent_id,
        granted_by=user.principal_id,
        cap=body.cap,
        valid_from=body.valid_from,
        valid_until=body.valid_until,
    )
    return GrantResponse(grant_id=grant.id)


# ---------------------------------------------------------------- token exchange


class TokenResponse(BaseModel):
    access_token: str
    issued_token_type: str = ACCESS_TOKEN_TYPE
    token_type: str = "Bearer"  # noqa: S105
    expires_in: int = AGENT_TOKEN_TTL_S


@router.post("/token", response_model=TokenResponse)
async def token_exchange(
    session: Annotated[AsyncSession, Depends(get_session)],
    verifier: Annotated[OIDCVerifier, Depends(get_verifier)],
    grant_type: Annotated[str, Form()],
    subject_token: Annotated[str, Form()],
    client_id: Annotated[str, Form()],
    client_secret: Annotated[str, Form()],
    subject_token_type: Annotated[str, Form()] = ACCESS_TOKEN_TYPE,
) -> TokenResponse:
    if grant_type != TOKEN_EXCHANGE_GRANT or subject_token_type != ACCESS_TOKEN_TYPE:
        raise UnsupportedGrantError(
            "grant_type no soportado.",
            hint=f"Usa grant_type={TOKEN_EXCHANGE_GRANT}.",
        )

    repo = PrincipalRepository(session)
    try:
        agent_uuid = UUID(client_id)
    except ValueError as exc:
        raise AgentAuthFailedError("Credenciales de agente inválidas.") from exc
    agent = await repo.get_agent(agent_uuid)
    if agent is None or not verify_secret(agent, client_secret):
        raise AgentAuthFailedError(
            "Credenciales de agente inválidas.",
            hint="Verifica client_id y client_secret del agente.",
        )
    agent_principal = await session.get(Principal, agent.principal_id, populate_existing=True)
    assert agent_principal is not None
    if agent_principal.status != PrincipalStatus.active:
        raise AgentSuspendedError("El agente está suspendido.")

    claims = await asyncio.to_thread(verifier.verify, subject_token)
    subject = await IdentityBridge(session).resolve(claims)
    if subject.principal_id != agent.owner_user_id:
        raise DelegationNotAllowedError(
            "El agente solo puede actuar en nombre de su dueño.",
            hint="La delegación a terceros llega en una fase posterior.",
        )

    grants = await repo.effective_grants(agent.principal_id)
    cap = merge_caps([g.cap for g in grants])
    if cap is None:
        raise NoCapabilitiesError(
            "El agente no tiene capacidades vigentes.",
            hint="El dueño debe otorgar al menos un capability grant.",
        )

    token, _ = issue_agent_token(
        agent_id=agent.principal_id,
        acting_for_user_id=subject.principal_id,
        tenant=subject.tenant,
        cap=cap,
    )
    return TokenResponse(access_token=token)


@router.get("/jwks")
async def jwks() -> dict[str, Any]:
    return public_jwks()


# ---------------------------------------------------------------- approvals


@dataclass(frozen=True)
class AgentContext:
    agent_id: UUID
    acting_user_id: UUID
    tenant: str
    token_jti: str | None


async def current_agent(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)],
) -> AgentContext:
    if credentials is None:
        raise TokenInvalidError("Falta el header Authorization.")
    verifier = get_iam_verifier()
    claims = await asyncio.to_thread(verifier.verify, credentials.credentials)
    sub = str(claims.get("sub", ""))
    if not sub.startswith("agent:"):
        raise TokenInvalidError("Se requiere un token de agente emitido por ordo-iam.")
    return AgentContext(
        agent_id=UUID(sub.removeprefix("agent:")),
        acting_user_id=UUID(str(claims["act"]["sub"]).removeprefix("user:")),
        tenant=str(claims["tenant"]),
        token_jti=claims.get("jti"),
    )


class CreateApprovalRequest(BaseModel):
    operation: dict[str, Any]


class ApprovalResponse(BaseModel):
    approval_id: UUID
    status: str
    expires_at: datetime
    operation_hash: str


def _approval_response(request_row: Any) -> ApprovalResponse:
    return ApprovalResponse(
        approval_id=request_row.id,
        status=request_row.status.value,
        expires_at=request_row.expires_at,
        operation_hash=request_row.operation_hash,
    )


@router.post("/approvals", response_model=ApprovalResponse, status_code=201)
async def create_approval(
    body: CreateApprovalRequest,
    agent: Annotated[AgentContext, Depends(current_agent)],
    session: Annotated[AsyncSession, Depends(get_session)],
    response: Response,
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> ApprovalResponse:
    if not idempotency_key:
        raise IdempotencyKeyRequiredError(
            "Falta el header Idempotency-Key.",
            hint="Toda solicitud de aprobación es idempotente por clave.",
        )
    request_row, created = await ApprovalService(session).create(
        tenant=agent.tenant,
        agent_id=agent.agent_id,
        requested_by=agent.acting_user_id,
        operation=body.operation,
        idempotency_key=idempotency_key,
    )
    if not created:
        response.status_code = 200
    return _approval_response(request_row)


@router.get("/approvals/{approval_id}", response_model=ApprovalResponse)
async def get_approval(
    approval_id: UUID,
    agent: Annotated[AgentContext, Depends(current_agent)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> ApprovalResponse:
    request_row = await ApprovalService(session).get(approval_id)
    if request_row.agent_id != agent.agent_id or request_row.tenant != agent.tenant:
        raise ApprovalNotFoundError("Solicitud de aprobación no encontrada.")
    return _approval_response(request_row)


class ResolveBody(BaseModel):
    reason: str | None = None


@router.post("/approvals/{approval_id}/approve", response_model=ApprovalResponse)
async def approve_approval(
    approval_id: UUID,
    user: Annotated[User, Depends(current_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
    body: ResolveBody | None = None,
) -> ApprovalResponse:
    request_row = await ApprovalService(session).resolve(
        approval_id,
        approver_id=user.principal_id,
        approve=True,
        reason=body.reason if body else None,
    )
    return _approval_response(request_row)


@router.post("/approvals/{approval_id}/reject", response_model=ApprovalResponse)
async def reject_approval(
    approval_id: UUID,
    user: Annotated[User, Depends(current_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
    body: ResolveBody | None = None,
) -> ApprovalResponse:
    request_row = await ApprovalService(session).resolve(
        approval_id,
        approver_id=user.principal_id,
        approve=False,
        reason=body.reason if body else None,
    )
    return _approval_response(request_row)


class ConsumeBody(BaseModel):
    operation: dict[str, Any]


@router.post("/approvals/{approval_id}/consume", response_model=ApprovalResponse)
async def consume_approval(
    approval_id: UUID,
    body: ConsumeBody,
    agent: Annotated[AgentContext, Depends(current_agent)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> ApprovalResponse:
    request_row = await ApprovalService(session).consume(
        approval_id, agent_id=agent.agent_id, operation=body.operation
    )
    return _approval_response(request_row)


# ---------------------------------------------------------------- authorize


@lru_cache(maxsize=1)
def get_iam_verifier() -> OIDCVerifier:
    """Verifier de los tokens emitidos por ordo-iam (agentes)."""
    from joserfc.jwk import KeySet

    from ordo_iam.keys import issuer, signing_key

    return OIDCVerifier(issuer=issuer(), audience="ordo-api", static_jwks=KeySet([signing_key()]))


@lru_cache(maxsize=1)
def get_usage_counter() -> UsageCounter:
    return RedisUsageCounter()


class AmountBody(BaseModel):
    currency: str
    value: str  # decimal como string, nunca float (CLAUDE.md §2.3)


class AuthorizeRequest(BaseModel):
    model: str
    operation: str
    amount: AmountBody | None = None


class AuthorizeResponse(BaseModel):
    allowed: bool
    reason: str
    requires_approval: bool
    record_domain: dict[str, Any]


@router.post("/authorize", response_model=AuthorizeResponse)
async def authorize(
    body: AuthorizeRequest,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)],
    session: Annotated[AsyncSession, Depends(get_session)],
    verifier: Annotated[OIDCVerifier, Depends(get_verifier)],
    iam_verifier: Annotated[OIDCVerifier, Depends(get_iam_verifier)],
    counter: Annotated[UsageCounter, Depends(get_usage_counter)],
) -> AuthorizeResponse:
    if credentials is None:
        raise TokenInvalidError("Falta el header Authorization.")
    token_str = credentials.credentials

    cap: dict[str, Any] | None = None
    agent_id: str | None = None
    act_chain: list[Any] = []
    token_jti: str | None = None
    try:
        claims = await asyncio.to_thread(iam_verifier.verify, token_str)
        # token de agente emitido por ordo-iam
        agent_id = str(claims["sub"]).removeprefix("agent:")
        cap = claims.get("cap")
        act_chain = [claims.get("act", {})]
        token_jti = claims.get("jti")
        user_id = UUID(str(claims["act"]["sub"]).removeprefix("user:"))
        tenant = str(claims["tenant"])
    except TokenInvalidError:
        claims = await asyncio.to_thread(verifier.verify, token_str)
        user = await IdentityBridge(session).resolve(claims)
        user_id = user.principal_id
        tenant = user.tenant

    amount = None
    if body.amount is not None:
        amount = Amount(body.amount.currency, Decimal(body.amount.value))
    request = AccessRequest(
        tenant=tenant,
        model=body.model,
        operation=body.operation,
        amount=amount,
        agent_id=agent_id,
        user_id=user_id,
    )
    decision = await PolicyEngine(session, counter).evaluate(request, cap=cap)
    await append_audit(
        session,
        tenant=tenant,
        event_type="authorize",
        payload={
            "model": body.model,
            "operation": body.operation,
            "amount": body.amount.model_dump() if body.amount else None,
            "allowed": decision.allowed,
            "reason": decision.reason,
            "requires_approval": decision.requires_approval,
        },
        principal_id=UUID(agent_id) if agent_id else user_id,
        act_chain=act_chain,
        token_jti=token_jti,
    )
    return AuthorizeResponse(
        allowed=decision.allowed,
        reason=decision.reason,
        requires_approval=decision.requires_approval,
        record_domain=decision.record_domain,
    )
