"""Execution environment: tenant, company and user context (F2-01, ADR-002).

Every query goes through here. Tenant isolation is enforced twice:
schema-per-tenant via `search_path` and Postgres RLS via a session GUC.
Domain code never writes the schema name or a tenant filter itself.
"""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass
from dataclasses import field as dc_field
from typing import Any

from sqlalchemy import event, text
from sqlalchemy.ext.asyncio import AsyncSession

from ordo_core.errors import KernelError
from ordo_core.registry import Registry

TENANT_RE = re.compile(r"^[a-z][a-z0-9_]{1,62}$")
TENANT_GUC = "ordo.tenant"
# Rol sin BYPASSRLS: si la conexión llegara con un rol privilegiado, RLS
# quedaría inerte y el aislamiento entre tenants dependería de una sola capa.
APP_ROLE = "ordo_app"
ROLE_RE = re.compile(r"^[a-z][a-z0-9_]{1,62}$")


def schema_for(tenant: str) -> str:
    """Schema name for a tenant. Validated: it is interpolated into DDL/SET."""
    if not TENANT_RE.match(tenant):
        raise KernelError(
            "TENANT_INVALID",
            f"Nombre de tenant inválido: {tenant!r}",
            hint="Solo minúsculas, dígitos y guion bajo; debe empezar con letra.",
        )
    return f"t_{tenant}"


@dataclass
class Environment:
    session: AsyncSession
    tenant: str
    registry: Registry
    user_id: uuid.UUID | None = None
    agent_id: uuid.UUID | None = None
    companies: list[int] = dc_field(default_factory=list)
    lang: str = "es_CL"
    tz: str = "UTC"
    context: dict[str, Any] = dc_field(default_factory=dict)
    app_role: str | None = APP_ROLE

    def __post_init__(self) -> None:
        self.schema = schema_for(self.tenant)
        if self.app_role is not None and not ROLE_RE.match(self.app_role):
            raise KernelError("APP_ROLE_INVALID", f"Rol de aplicación inválido: {self.app_role!r}")

    async def bind(self) -> None:
        """Pin schema and tenant GUC for the current transaction.

        The settings are transaction-scoped, so a commit inside a request
        would silently drop them. A listener re-applies the binding on every
        new transaction of this session: the tenant filter can never be lost
        halfway through a request.

        `set_config(..., is_local => true)` takes bound parameters, so no
        value is ever interpolated into SQL (AGENTS.md §2.5).
        """
        self._install_rebind_listener()
        await self.session.execute(
            text("SELECT set_config('search_path', :path, true)"),
            {"path": f"{self.schema},public"},
        )
        await self.session.execute(
            text(f"SELECT set_config('{TENANT_GUC}', :tenant, true)"),
            {"tenant": self.tenant},
        )
        if self.app_role is not None:
            # Defensa en profundidad: aunque el DSN traiga un rol privilegiado,
            # la transacción corre como rol sujeto a RLS.
            await self.session.execute(text(f"SET LOCAL ROLE {self.app_role}"))

    def _install_rebind_listener(self) -> None:
        if getattr(self, "_listener_installed", False):
            return
        sync_session = self.session.sync_session
        schema, tenant, app_role = self.schema, self.tenant, self.app_role

        @event.listens_for(sync_session, "after_begin")
        def _rebind(session: Any, transaction: Any, connection: Any) -> None:
            connection.execute(
                text("SELECT set_config('search_path', :path, true)"),
                {"path": f"{schema},public"},
            )
            connection.execute(
                text(f"SELECT set_config('{TENANT_GUC}', :tenant, true)"),
                {"tenant": tenant},
            )
            if app_role is not None:
                connection.execute(text(f"SET LOCAL ROLE {app_role}"))

        self._listener_installed = True

    @property
    def company_id(self) -> int | None:
        return self.companies[0] if self.companies else None

    def with_context(self, **overrides: Any) -> Environment:
        return Environment(
            session=self.session,
            tenant=self.tenant,
            registry=self.registry,
            user_id=self.user_id,
            agent_id=self.agent_id,
            companies=list(self.companies),
            lang=self.lang,
            tz=self.tz,
            context={**self.context, **overrides},
        )
