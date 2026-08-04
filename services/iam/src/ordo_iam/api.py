"""HTTP API of ordo-iam (F1-02 /me, F1-03 agents + token exchange)."""

from __future__ import annotations

import asyncio
import os
from datetime import datetime
from functools import lru_cache
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, Form
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from ordo_iam.agent_auth import new_credentials, verify_secret
from ordo_iam.bridge import IdentityBridge
from ordo_iam.captokens import merge_caps
from ordo_iam.db import get_session
from ordo_iam.errors import (
    AgentAuthFailedError,
    AgentSuspendedError,
    DelegationNotAllowedError,
    NoCapabilitiesError,
    NotAgentOwnerError,
    TokenInvalidError,
    UnsupportedGrantError,
)
from ordo_iam.keys import public_jwks
from ordo_iam.models import AutonomyLevel, Principal, PrincipalStatus, User
from ordo_iam.oidc import OIDCVerifier
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
