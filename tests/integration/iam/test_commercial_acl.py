"""Los roles declarados por los módulos, cargados en IAM y evaluados por el PDP."""

import uuid
from pathlib import Path

import pytest
from ordo_iam.models import Role, RoleMember
from ordo_iam.pdp import AccessRequest, InMemoryUsageCounter, PolicyEngine
from ordo_iam.repository import PrincipalRepository
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

pytestmark = pytest.mark.integration

REPO_ROOT = Path(__file__).resolve().parents[3]
TENANT = "aclcorp"


async def seed_roles(session: AsyncSession) -> None:
    import sys

    sys.path.insert(0, str(REPO_ROOT / "tools"))
    from seed_iam_roles import seed

    await seed(session, TENANT)


async def user_with_role(session: AsyncSession, role_name: str) -> uuid.UUID:
    repo = PrincipalRepository(session)
    user = await repo.create_user(
        tenant=TENANT,
        email=f"{role_name}-{uuid.uuid4().hex[:6]}@acme.cl",
        display_name=role_name,
    )
    role = await session.scalar(select(Role).where(Role.tenant == TENANT, Role.name == role_name))
    assert role is not None
    session.add(RoleMember(role_id=role.id, principal_id=user.principal_id))
    await session.commit()
    return user.principal_id


def request(model: str, operation: str, user_id: uuid.UUID) -> AccessRequest:
    return AccessRequest(tenant=TENANT, model=model, operation=operation, user_id=user_id)


@pytest.fixture
async def engine_and_users(session: AsyncSession) -> dict:  # type: ignore[type-arg]
    await seed_roles(session)
    return {
        "engine": PolicyEngine(session, InMemoryUsageCounter()),
        "vendedor": await user_with_role(session, "ventas"),
        "contador": await user_with_role(session, "contabilidad"),
        "auditor": await user_with_role(session, "auditor"),
    }


class TestCommercialRoles:
    async def test_seeding_twice_is_idempotent(self, session: AsyncSession) -> None:
        await seed_roles(session)
        await seed_roles(session)
        roles = (await session.scalars(select(Role).where(Role.tenant == TENANT))).all()
        names = [role.name for role in roles]
        assert len(names) == len(set(names))

    async def test_sales_operates_orders_but_not_accounting(
        self,
        engine_and_users: dict,  # type: ignore[type-arg]
    ) -> None:
        engine = engine_and_users["engine"]
        vendedor = engine_and_users["vendedor"]

        allowed = await engine.evaluate(request("sale.order", "create", vendedor), cap=None)
        assert allowed.allowed

        reads_moves = await engine.evaluate(request("account.move", "read", vendedor), cap=None)
        assert reads_moves.allowed

        writes_moves = await engine.evaluate(request("account.move", "write", vendedor), cap=None)
        assert not writes_moves.allowed
        assert writes_moves.reason == "RBAC_DENIED"

    async def test_actions_map_to_write(self, engine_and_users: dict) -> None:  # type: ignore[type-arg]
        """action_invoice no es CRUD: el PDP la evalúa como write del modelo."""
        engine = engine_and_users["engine"]
        vendedor = engine_and_users["vendedor"]
        contador = engine_and_users["contador"]

        invoice = await engine.evaluate(request("sale.order", "action_invoice", vendedor), cap=None)
        assert invoice.allowed

        post_as_sales = await engine.evaluate(
            request("account.move", "action_post", vendedor), cap=None
        )
        assert not post_as_sales.allowed

        post_as_accountant = await engine.evaluate(
            request("account.move", "action_post", contador), cap=None
        )
        assert post_as_accountant.allowed

    async def test_auditor_reads_everything_writes_nothing(
        self,
        engine_and_users: dict,  # type: ignore[type-arg]
    ) -> None:
        engine = engine_and_users["engine"]
        auditor = engine_and_users["auditor"]
        for model in ("sale.order", "account.move", "edi.document", "account.payment"):
            read = await engine.evaluate(request(model, "read", auditor), cap=None)
            assert read.allowed, model
            write = await engine.evaluate(request(model, "write", auditor), cap=None)
            assert not write.allowed, model

    async def test_nobody_deletes_posted_history(self, engine_and_users: dict) -> None:  # type: ignore[type-arg]
        engine = engine_and_users["engine"]
        for who in ("vendedor", "contador", "auditor"):
            decision = await engine.evaluate(
                request("account.move", "unlink", engine_and_users[who]), cap=None
            )
            assert not decision.allowed, who

    async def test_no_role_means_no_access(self, session: AsyncSession) -> None:
        await seed_roles(session)
        repo = PrincipalRepository(session)
        user = await repo.create_user(
            tenant=TENANT, email=f"nadie-{uuid.uuid4().hex[:6]}@acme.cl", display_name="N"
        )
        engine = PolicyEngine(session, InMemoryUsageCounter())
        decision = await engine.evaluate(request("sale.order", "read", user.principal_id), cap=None)
        assert not decision.allowed
