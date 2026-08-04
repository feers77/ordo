"""HTTP API of ordo-iam (F1-02: /iam/v1/me)."""

from __future__ import annotations

import asyncio
import os
from functools import lru_cache
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from ordo_iam.bridge import IdentityBridge
from ordo_iam.db import get_session
from ordo_iam.errors import TokenInvalidError
from ordo_iam.models import Principal
from ordo_iam.oidc import OIDCVerifier

router = APIRouter(prefix="/iam/v1", tags=["iam"])

bearer = HTTPBearer(auto_error=False)


@lru_cache(maxsize=1)
def get_verifier() -> OIDCVerifier:
    issuer = os.environ.get("OIDC_ISSUER", "http://127.0.0.1:8080/realms/ordo")
    audience = os.environ.get("OIDC_AUDIENCE", "ordo-api")
    return OIDCVerifier(issuer=issuer, audience=audience, jwks_url=os.environ.get("OIDC_JWKS_URL"))


class MeResponse(BaseModel):
    principal_id: UUID
    tenant: str
    type: str
    display_name: str
    email: str


@router.get("/me", response_model=MeResponse)
async def me(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)],
    session: Annotated[AsyncSession, Depends(get_session)],
    verifier: Annotated[OIDCVerifier, Depends(get_verifier)],
) -> MeResponse:
    if credentials is None:
        raise TokenInvalidError(
            "Falta el header Authorization.",
            hint="Envía 'Authorization: Bearer <access_token>'.",
        )
    claims = await asyncio.to_thread(verifier.verify, credentials.credentials)
    user = await IdentityBridge(session).resolve(claims)
    principal = await session.get(Principal, user.principal_id)
    assert principal is not None
    return MeResponse(
        principal_id=user.principal_id,
        tenant=user.tenant,
        type=principal.type.value,
        display_name=principal.display_name,
        email=user.email,
    )
