"""Tests de invariantes del modelo de principals (F1-01) — escritos ANTES de implementar.

Cubren los invariantes 1-7 del diseño y los códigos de error estables.
"""

from datetime import UTC, datetime, timedelta

import pytest
from ordo_iam.errors import (
    EmailTakenError,
    OwnerInactiveError,
    OwnerNotFoundError,
    TenantMismatchError,
)
from ordo_iam.repository import PrincipalRepository
from sqlalchemy.ext.asyncio import AsyncSession

pytestmark = pytest.mark.integration

CAP_READ_SALES = {"models": {"sale.order": ["read"]}}


async def make_user(repo: PrincipalRepository, tenant: str = "acme", email: str = "ana@acme.cl"):  # type: ignore[no-untyped-def]
    return await repo.create_user(tenant=tenant, email=email, display_name="Ana")


class TestUserInvariants:
    async def test_email_unique_per_tenant(self, session: AsyncSession) -> None:
        repo = PrincipalRepository(session)
        await make_user(repo, email="dup@acme.cl")
        with pytest.raises(EmailTakenError) as exc:
            await make_user(repo, email="dup@acme.cl")
        assert exc.value.code == "IAM_EMAIL_TAKEN"

    async def test_same_email_other_tenant_ok(self, session: AsyncSession) -> None:
        repo = PrincipalRepository(session)
        await make_user(repo, tenant="acme", email="x@corp.cl")
        user = await make_user(repo, tenant="globex", email="x@corp.cl")
        assert user.tenant == "globex"

    async def test_email_unique_case_insensitive(self, session: AsyncSession) -> None:
        repo = PrincipalRepository(session)
        await make_user(repo, email="Case@Acme.cl")
        with pytest.raises(EmailTakenError):
            await make_user(repo, email="case@acme.cl")

    async def test_timestamps_are_utc_aware(self, session: AsyncSession) -> None:
        repo = PrincipalRepository(session)
        user = await make_user(repo, email="tz@acme.cl")
        assert user.create_date.tzinfo is not None
        assert abs(user.create_date - datetime.now(UTC)) < timedelta(minutes=1)


class TestAgentInvariants:
    async def test_agent_requires_existing_owner(self, session: AsyncSession) -> None:
        repo = PrincipalRepository(session)
        import uuid

        with pytest.raises(OwnerNotFoundError) as exc:
            await repo.create_agent(
                tenant="acme",
                owner_user_id=uuid.uuid4(),
                display_name="bot",
                model="claude-fable-5",
            )
        assert exc.value.code == "IAM_OWNER_NOT_FOUND"

    async def test_agent_owner_must_be_active(self, session: AsyncSession) -> None:
        repo = PrincipalRepository(session)
        user = await make_user(repo, email="susp@acme.cl")
        await repo.suspend_principal(user.principal_id)
        with pytest.raises(OwnerInactiveError):
            await repo.create_agent(
                tenant="acme",
                owner_user_id=user.principal_id,
                display_name="bot",
                model="claude-fable-5",
            )

    async def test_agent_owner_same_tenant(self, session: AsyncSession) -> None:
        repo = PrincipalRepository(session)
        user = await make_user(repo, tenant="acme", email="cross@acme.cl")
        with pytest.raises(TenantMismatchError):
            await repo.create_agent(
                tenant="globex",
                owner_user_id=user.principal_id,
                display_name="bot",
                model="claude-fable-5",
            )

    async def test_autonomy_defaults_to_observer(self, session: AsyncSession) -> None:
        repo = PrincipalRepository(session)
        user = await make_user(repo, email="own1@acme.cl")
        agent = await repo.create_agent(
            tenant="acme",
            owner_user_id=user.principal_id,
            display_name="bot",
            model="claude-fable-5",
        )
        assert agent.autonomy_level == "observer"

    async def test_suspending_owner_suspends_agents(self, session: AsyncSession) -> None:
        repo = PrincipalRepository(session)
        user = await make_user(repo, email="own2@acme.cl")
        agent = await repo.create_agent(
            tenant="acme",
            owner_user_id=user.principal_id,
            display_name="bot",
            model="claude-fable-5",
        )
        await repo.suspend_principal(user.principal_id)
        refreshed = await repo.get_principal(agent.principal_id)
        assert refreshed is not None
        assert refreshed.status == "suspended"


class TestCapabilityGrants:
    async def _agent(self, repo: PrincipalRepository, email: str):  # type: ignore[no-untyped-def]
        user = await make_user(repo, email=email)
        agent = await repo.create_agent(
            tenant="acme",
            owner_user_id=user.principal_id,
            display_name="bot",
            model="claude-fable-5",
        )
        return user, agent

    async def test_no_grants_means_no_capabilities(self, session: AsyncSession) -> None:
        repo = PrincipalRepository(session)
        _, agent = await self._agent(repo, "g0@acme.cl")
        assert await repo.effective_grants(agent.principal_id) == []

    async def test_active_grant_is_effective(self, session: AsyncSession) -> None:
        repo = PrincipalRepository(session)
        user, agent = await self._agent(repo, "g1@acme.cl")
        await repo.grant_capability(
            agent_id=agent.principal_id,
            granted_by=user.principal_id,
            cap=CAP_READ_SALES,
        )
        grants = await repo.effective_grants(agent.principal_id)
        assert len(grants) == 1
        assert grants[0].cap == CAP_READ_SALES

    async def test_revoked_grant_not_effective(self, session: AsyncSession) -> None:
        repo = PrincipalRepository(session)
        user, agent = await self._agent(repo, "g2@acme.cl")
        grant = await repo.grant_capability(
            agent_id=agent.principal_id, granted_by=user.principal_id, cap=CAP_READ_SALES
        )
        await repo.revoke_grant(grant.id)
        assert await repo.effective_grants(agent.principal_id) == []

    async def test_expired_grant_not_effective(self, session: AsyncSession) -> None:
        repo = PrincipalRepository(session)
        user, agent = await self._agent(repo, "g3@acme.cl")
        await repo.grant_capability(
            agent_id=agent.principal_id,
            granted_by=user.principal_id,
            cap=CAP_READ_SALES,
            valid_until=datetime.now(UTC) - timedelta(seconds=1),
        )
        assert await repo.effective_grants(agent.principal_id) == []

    async def test_future_grant_not_effective_yet(self, session: AsyncSession) -> None:
        repo = PrincipalRepository(session)
        user, agent = await self._agent(repo, "g4@acme.cl")
        await repo.grant_capability(
            agent_id=agent.principal_id,
            granted_by=user.principal_id,
            cap=CAP_READ_SALES,
            valid_from=datetime.now(UTC) + timedelta(hours=1),
        )
        assert await repo.effective_grants(agent.principal_id) == []
