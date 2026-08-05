"""Sembrar el primer usuario de un tenant: sin esto, IAM se niega a todo."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import pytest
from ordo_iam.bridge import IdentityBridge
from ordo_iam.errors import UnknownIdentityError
from ordo_iam.models import Principal, PrincipalType, Role, RoleMember, User
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
from tools.seed_iam_user import seed_user

pytestmark = pytest.mark.integration


async def make_role(session: AsyncSession, tenant: str, name: str) -> Role:
    role = Role(tenant=tenant, name=name)
    session.add(role)
    await session.commit()
    return role


class TestSeedUser:
    async def test_the_user_is_created_ready_for_its_first_login(
        self, session: AsyncSession
    ) -> None:
        result = await seed_user(session, "ropa", "duena@ropa.cl", display_name="Dueña", roles=[])
        assert result["created"] is True
        assert result["linked"] is False  # el bridge lo vincula en el primer login

        user = await session.scalar(select(User).where(func.lower(User.email) == "duena@ropa.cl"))
        assert user is not None
        assert user.tenant == "ropa"
        assert user.idp_sub is None
        principal = await session.get(Principal, user.principal_id)
        assert principal is not None
        assert principal.type == PrincipalType.user
        assert principal.display_name == "Dueña"

    async def test_seeding_twice_does_not_duplicate(self, session: AsyncSession) -> None:
        await seed_user(session, "ropa", "duena@ropa.cl", display_name="Dueña", roles=[])
        again = await seed_user(
            session, "ropa", "DUENA@ropa.cl", display_name="Dueña Demo", roles=[]
        )
        assert again["created"] is False

        users = await session.scalars(
            select(User).where(User.tenant == "ropa", func.lower(User.email) == "duena@ropa.cl")
        )
        assert len(list(users)) == 1
        principal = await session.get(Principal, again["principal_id"])
        assert principal is not None
        assert principal.display_name == "Dueña Demo"

    async def test_roles_are_granted_once(self, session: AsyncSession) -> None:
        await make_role(session, "ropa", "cajero")
        first = await seed_user(
            session, "ropa", "caja@ropa.cl", display_name="Caja", roles=["cajero"]
        )
        assert first["granted"] == ["cajero"]

        second = await seed_user(
            session, "ropa", "caja@ropa.cl", display_name="Caja", roles=["cajero"]
        )
        assert second["granted"] == []  # ya era miembro

        members = await session.scalars(
            select(RoleMember).where(RoleMember.principal_id == first["principal_id"])
        )
        assert len(list(members)) == 1

    async def test_an_unknown_role_is_reported_not_invented(self, session: AsyncSession) -> None:
        """Crear un rol vacío daría una membresía que no autoriza nada, y quien
        despliega creería que quedó configurado."""
        result = await seed_user(
            session, "ropa", "bodega@ropa.cl", display_name="Bodega", roles=["inventario"]
        )
        assert result["granted"] == []
        assert result["missing_roles"] == ["inventario"]


class TestWithoutSeeding:
    async def test_the_bridge_refuses_an_unknown_identity(self, session: AsyncSession) -> None:
        """Esta es la razón de que el script exista: sin usuario sembrado, IAM
        se niega y no hay forma soportada de crearlo."""
        claims: dict[str, Any] = {
            "sub": "kc-nadie",
            "tenant": "ropa",
            "email": "fantasma@ropa.cl",
            "email_verified": True,
        }
        with pytest.raises(UnknownIdentityError):
            await IdentityBridge(session).resolve(claims)

    async def test_after_seeding_the_first_login_binds(self, session: AsyncSession) -> None:
        await seed_user(session, "ropa", "duena@ropa.cl", display_name="Dueña", roles=[])
        claims: dict[str, Any] = {
            "sub": "kc-duena",
            "tenant": "ropa",
            "email": "duena@ropa.cl",
            "email_verified": True,
        }
        user = await IdentityBridge(session).resolve(claims)
        assert user.idp_sub == "kc-duena"
        assert user.tenant == "ropa"
