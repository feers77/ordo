"""Sandbox endpoints: clone the tenant, rehearse, throw it away (F3-03 §3).

Cloning a schema is DDL and the application role does not have DDL
(AGENTS.md §7). These endpoints therefore use a separate connection,
declared in `ORDO_ADMIN_DATABASE_URL`; without it the answer is a 503, never
a quiet privilege upgrade of the role that serves normal traffic.
"""

from __future__ import annotations

import os
from typing import TYPE_CHECKING, Annotated, Any

from fastapi import APIRouter, Depends
from ordo_core import Environment
from ordo_core.sandbox import (
    SANDBOX_MARKER,
    SandboxError,
    create_sandbox,
    drop_sandbox,
    ensure_registry_table,
    list_sandboxes,
)
from ordo_runtime import OrdoError
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from ordo_api.deps import get_env
from ordo_api.records import _wrap

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

router = APIRouter(prefix="/api/v1", tags=["sandbox"])

DEFAULT_TTL_HOURS = 24

_admin_engine: AsyncEngine | None = None


def set_admin_engine(engine: AsyncEngine | None) -> None:
    """Injects the admin engine (tests); `None` restores the env lookup."""
    global _admin_engine
    _admin_engine = engine


def admin_engine() -> AsyncEngine:
    """Owner-role engine, cached per process (same pattern as `deps`)."""
    global _admin_engine
    if _admin_engine is None:
        url = os.environ.get("ORDO_ADMIN_DATABASE_URL")
        if not url:
            raise OrdoError(
                "El sandbox necesita ORDO_ADMIN_DATABASE_URL configurada.",
                code="SANDBOX_UNAVAILABLE",
                status_code=503,
                hint="Clonar un schema es DDL: el rol de la aplicación no lo tiene a propósito.",
            )
        _admin_engine = create_async_engine(url, pool_size=2, max_overflow=2)
    return _admin_engine


async def admin_session() -> AsyncIterator[AsyncSession]:
    """One admin session per request, closed when the request ends."""
    maker = async_sessionmaker(admin_engine(), expire_on_commit=False)
    async with maker() as session:
        yield session


def _default_ttl_hours() -> int:
    raw = os.environ.get("SANDBOX_TTL_HOURS", "")
    return int(raw) if raw.isdigit() else DEFAULT_TTL_HOURS


class SandboxRequest(BaseModel):
    """Optional body of `POST /sandbox`."""

    ttl_hours: int | None = Field(
        default=None,
        ge=0,
        description="Horas hasta que el sandbox caduque; por defecto SANDBOX_TTL_HOURS.",
    )


@router.post("/sandbox", status_code=201)
async def create(
    env: Annotated[Environment, Depends(get_env)],
    session: Annotated[AsyncSession, Depends(admin_session)],
    body: SandboxRequest | None = None,
) -> dict[str, Any]:
    """Clones the current tenant into an ephemeral one.

    No `Idempotency-Key` here on purpose: creating a sandbox is cheap and
    disposable, and a repeated call yields another throwaway schema instead
    of corrupting anything.
    """
    requested = body.ttl_hours if body is not None else None
    ttl = requested if requested is not None else _default_ttl_hours()
    await ensure_registry_table(session)
    try:
        return await create_sandbox(session, env.tenant, ttl_hours=ttl)
    except SandboxError as exc:
        raise _wrap(exc) from exc


@router.get("/sandbox")
async def index(
    env: Annotated[Environment, Depends(get_env)],
    session: Annotated[AsyncSession, Depends(admin_session)],
) -> dict[str, Any]:
    """Sandboxes cloned from the current tenant."""
    await ensure_registry_table(session)
    return {"sandboxes": await list_sandboxes(session, env.tenant)}


# El segmento se llama `{sandbox}` y no `{tenant}` porque `get_env` ya declara
# un parámetro `tenant` (la cabecera X-Ordo-Tenant) y FastAPI lo tomaría por
# parámetro de ruta. La URL que ve el cliente es la misma.
@router.delete("/sandbox/{sandbox}")
async def destroy(
    sandbox: str,
    env: Annotated[Environment, Depends(get_env)],
    session: Annotated[AsyncSession, Depends(admin_session)],
) -> dict[str, Any]:
    """Drops one of the current tenant's sandboxes."""
    if not sandbox.startswith(f"{env.tenant}{SANDBOX_MARKER}"):
        raise OrdoError(
            "Ese sandbox no pertenece a este tenant.",
            code="SANDBOX_FOREIGN",
            status_code=403,
            hint="Solo puedes borrar sandboxes creados desde tu propio tenant.",
        )
    await ensure_registry_table(session)
    try:
        await drop_sandbox(session, sandbox)
    except SandboxError as exc:
        raise _wrap(exc) from exc
    return {"dropped": sandbox}
