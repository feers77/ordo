"""Identity bridge: OIDC claims → iam_user (design F1-02).

Never auto-creates users (deny by default). First verified login binds
the IdP subject to a pre-provisioned user of the same tenant.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ordo_iam.errors import (
    PrincipalSuspendedError,
    TokenInvalidError,
    UnknownIdentityError,
)
from ordo_iam.models import Principal, PrincipalStatus, User


class IdentityBridge:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def resolve(self, claims: dict[str, Any]) -> User:
        sub = claims.get("sub")
        tenant = claims.get("tenant")
        if not sub or not tenant:
            raise TokenInvalidError("Token sin sub o tenant.")

        user = await self.session.scalar(select(User).where(User.idp_sub == sub))
        if user is None:
            user = await self._link_first_login(claims, sub=sub, tenant=tenant)
        elif user.tenant != tenant:
            raise UnknownIdentityError(
                "La identidad no corresponde al tenant del token.",
                hint="El usuario vinculado pertenece a otro tenant.",
            )

        principal = await self.session.get(Principal, user.principal_id, populate_existing=True)
        assert principal is not None
        if principal.status != PrincipalStatus.active:
            raise PrincipalSuspendedError(
                "El principal está suspendido.",
                hint="Contacta a un administrador del tenant.",
            )
        return user

    async def _link_first_login(self, claims: dict[str, Any], *, sub: str, tenant: str) -> User:
        email = claims.get("email")
        verified = claims.get("email_verified") is True
        if email and verified:
            user = await self.session.scalar(
                select(User).where(
                    User.tenant == tenant,
                    func.lower(User.email) == email.lower(),
                    User.idp_sub.is_(None),
                )
            )
            if user is not None:
                user.idp_sub = sub
                await self.session.commit()
                return user
        raise UnknownIdentityError(
            "Identidad no registrada en este tenant.",
            hint="Un administrador debe crear el usuario antes del primer login.",
        )
