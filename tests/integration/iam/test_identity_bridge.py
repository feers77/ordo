"""Tests del bridge de identidad (F1-02) — escritos ANTES de implementar."""

from typing import Any

import pytest
from ordo_iam.bridge import IdentityBridge
from ordo_iam.errors import PrincipalSuspendedError, UnknownIdentityError
from ordo_iam.repository import PrincipalRepository
from sqlalchemy.ext.asyncio import AsyncSession

pytestmark = pytest.mark.integration


def claims(sub: str = "kc-1", email: str = "ana@acme.cl", tenant: str = "acme") -> dict[str, Any]:
    return {"sub": sub, "email": email, "email_verified": True, "tenant": tenant}


class TestIdentityBridge:
    async def test_first_login_binds_idp_sub(self, session: AsyncSession) -> None:
        repo = PrincipalRepository(session)
        user = await repo.create_user(tenant="acme", email="ana@acme.cl", display_name="Ana")
        bridge = IdentityBridge(session)
        resolved = await bridge.resolve(claims(sub="kc-ana"))
        assert resolved.principal_id == user.principal_id
        assert resolved.idp_sub == "kc-ana"

    async def test_known_sub_resolves_directly(self, session: AsyncSession) -> None:
        repo = PrincipalRepository(session)
        await repo.create_user(tenant="acme", email="b@acme.cl", display_name="B")
        bridge = IdentityBridge(session)
        first = await bridge.resolve(claims(sub="kc-b", email="b@acme.cl"))
        again = await bridge.resolve(claims(sub="kc-b", email="cambiado@acme.cl"))
        assert again.principal_id == first.principal_id

    async def test_unknown_identity_rejected(self, session: AsyncSession) -> None:
        bridge = IdentityBridge(session)
        with pytest.raises(UnknownIdentityError) as exc:
            await bridge.resolve(claims(sub="kc-nadie", email="nadie@acme.cl"))
        assert exc.value.code == "IAM_UNKNOWN_IDENTITY"

    async def test_email_in_other_tenant_does_not_link(self, session: AsyncSession) -> None:
        repo = PrincipalRepository(session)
        await repo.create_user(tenant="globex", email="c@corp.cl", display_name="C")
        bridge = IdentityBridge(session)
        with pytest.raises(UnknownIdentityError):
            await bridge.resolve(claims(sub="kc-c", email="c@corp.cl", tenant="acme"))

    async def test_email_already_bound_to_other_sub_not_relinked(
        self, session: AsyncSession
    ) -> None:
        repo = PrincipalRepository(session)
        await repo.create_user(tenant="acme", email="d@acme.cl", display_name="D")
        bridge = IdentityBridge(session)
        await bridge.resolve(claims(sub="kc-d1", email="d@acme.cl"))
        with pytest.raises(UnknownIdentityError):
            await bridge.resolve(claims(sub="kc-d2", email="d@acme.cl"))

    async def test_unverified_email_does_not_link(self, session: AsyncSession) -> None:
        repo = PrincipalRepository(session)
        await repo.create_user(tenant="acme", email="e@acme.cl", display_name="E")
        bridge = IdentityBridge(session)
        c = claims(sub="kc-e", email="e@acme.cl")
        c["email_verified"] = False
        with pytest.raises(UnknownIdentityError):
            await bridge.resolve(c)

    async def test_suspended_user_rejected(self, session: AsyncSession) -> None:
        repo = PrincipalRepository(session)
        user = await repo.create_user(tenant="acme", email="f@acme.cl", display_name="F")
        bridge = IdentityBridge(session)
        await bridge.resolve(claims(sub="kc-f", email="f@acme.cl"))
        await repo.suspend_principal(user.principal_id)
        with pytest.raises(PrincipalSuspendedError) as exc:
            await bridge.resolve(claims(sub="kc-f", email="f@acme.cl"))
        assert exc.value.code == "IAM_PRINCIPAL_SUSPENDED"

    async def test_email_match_is_case_insensitive(self, session: AsyncSession) -> None:
        repo = PrincipalRepository(session)
        user = await repo.create_user(tenant="acme", email="G@Acme.cl", display_name="G")
        bridge = IdentityBridge(session)
        resolved = await bridge.resolve(claims(sub="kc-g", email="g@acme.cl"))
        assert resolved.principal_id == user.principal_id
