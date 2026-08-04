"""Repository for principals: creation, lifecycle and capability grants.

Enforces the invariants of design F1-01. All writes commit here so error
mapping (unique violations → stable codes) happens in one place.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import func, or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from ordo_iam.errors import (
    ClientIdTakenError,
    EmailTakenError,
    GrantNotFoundError,
    OwnerInactiveError,
    OwnerNotFoundError,
    PrincipalNotFoundError,
    TenantMismatchError,
)
from ordo_iam.models import (
    Agent,
    AutonomyLevel,
    CapabilityGrant,
    Principal,
    PrincipalStatus,
    PrincipalType,
    ServiceClient,
    User,
)


class PrincipalRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    # -- users ----------------------------------------------------------

    async def create_user(self, *, tenant: str, email: str, display_name: str) -> User:
        principal = Principal(type=PrincipalType.user, tenant=tenant, display_name=display_name)
        self.session.add(principal)
        await self.session.flush()
        user = User(principal_id=principal.id, tenant=tenant, email=email)
        self.session.add(user)
        try:
            await self.session.commit()
        except IntegrityError as exc:
            await self.session.rollback()
            raise EmailTakenError(
                f"Ya existe un usuario con email {email} en el tenant {tenant}.",
                hint="Usa otro email o recupera la cuenta existente.",
            ) from exc
        return user

    # -- service clients --------------------------------------------------

    async def create_service_client(
        self,
        *,
        tenant: str,
        client_id: str,
        display_name: str,
        allowed_scopes: list[str] | None = None,
    ) -> ServiceClient:
        principal = Principal(
            type=PrincipalType.service_client, tenant=tenant, display_name=display_name
        )
        self.session.add(principal)
        await self.session.flush()
        client = ServiceClient(
            principal_id=principal.id,
            client_id=client_id,
            allowed_scopes=allowed_scopes or [],
        )
        self.session.add(client)
        try:
            await self.session.commit()
        except IntegrityError as exc:
            await self.session.rollback()
            raise ClientIdTakenError(
                f"client_id {client_id} ya registrado.",
                hint="Los client_id son únicos globales.",
            ) from exc
        return client

    # -- agents -----------------------------------------------------------

    async def create_agent(
        self,
        *,
        tenant: str,
        owner_user_id: uuid.UUID,
        display_name: str,
        model: str,
        model_version: str | None = None,
        autonomy_level: AutonomyLevel = AutonomyLevel.observer,
        budget: dict[str, Any] | None = None,
        secret_hash: str | None = None,
        secret_salt: str | None = None,
    ) -> Agent:
        owner = await self.session.get(User, owner_user_id)
        if owner is None:
            raise OwnerNotFoundError(
                "El usuario dueño del agente no existe.",
                hint="Crea primero el usuario o verifica el owner_user_id.",
            )
        owner_principal = await self.session.get(Principal, owner_user_id, populate_existing=True)
        assert owner_principal is not None  # FK garantiza existencia
        if owner_principal.status != PrincipalStatus.active:
            raise OwnerInactiveError(
                "El usuario dueño no está activo.",
                hint="Reactiva al usuario antes de registrar agentes a su nombre.",
            )
        if owner.tenant != tenant:
            raise TenantMismatchError(
                "El dueño pertenece a otro tenant.",
                hint="Un agente y su dueño deben ser del mismo tenant.",
            )
        principal = Principal(type=PrincipalType.agent, tenant=tenant, display_name=display_name)
        self.session.add(principal)
        await self.session.flush()
        agent = Agent(
            principal_id=principal.id,
            owner_user_id=owner_user_id,
            model=model,
            model_version=model_version,
            autonomy_level=autonomy_level,
            budget=budget or {},
            secret_hash=secret_hash,
            secret_salt=secret_salt,
        )
        self.session.add(agent)
        await self.session.commit()
        return agent

    async def get_agent(self, agent_id: uuid.UUID) -> Agent | None:
        return await self.session.get(Agent, agent_id)

    # -- lifecycle ----------------------------------------------------------

    async def get_principal(self, principal_id: uuid.UUID) -> Principal | None:
        return await self.session.get(Principal, principal_id, populate_existing=True)

    async def suspend_principal(self, principal_id: uuid.UUID) -> None:
        """Suspend a principal; suspending a user cascades to their agents."""
        principal = await self.session.get(Principal, principal_id)
        if principal is None:
            raise PrincipalNotFoundError("Principal no encontrado.")
        principal.status = PrincipalStatus.suspended
        if principal.type == PrincipalType.user:
            agent_ids = select(Agent.principal_id).where(Agent.owner_user_id == principal_id)
            await self.session.execute(
                update(Principal)
                .where(Principal.id.in_(agent_ids))
                .values(status=PrincipalStatus.suspended)
            )
        await self.session.commit()

    # -- capability grants ---------------------------------------------------

    async def grant_capability(
        self,
        *,
        agent_id: uuid.UUID,
        granted_by: uuid.UUID,
        cap: dict[str, Any],
        valid_from: datetime | None = None,
        valid_until: datetime | None = None,
    ) -> CapabilityGrant:
        grant = CapabilityGrant(
            agent_id=agent_id,
            granted_by=granted_by,
            cap=cap,
            valid_from=valid_from or datetime.now(UTC),
            valid_until=valid_until,
        )
        self.session.add(grant)
        await self.session.commit()
        return grant

    async def revoke_grant(self, grant_id: uuid.UUID) -> None:
        grant = await self.session.get(CapabilityGrant, grant_id)
        if grant is None:
            raise GrantNotFoundError("Grant no encontrado.")
        grant.revoked_at = datetime.now(UTC)
        await self.session.commit()

    async def effective_grants(self, agent_id: uuid.UUID) -> list[CapabilityGrant]:
        """Grants vigentes: no revocados, dentro de su ventana de validez."""
        now = func.now()
        result = await self.session.scalars(
            select(CapabilityGrant).where(
                CapabilityGrant.agent_id == agent_id,
                CapabilityGrant.revoked_at.is_(None),
                CapabilityGrant.valid_from <= now,
                or_(
                    CapabilityGrant.valid_until.is_(None),
                    CapabilityGrant.valid_until > now,
                ),
            )
        )
        return list(result.all())
