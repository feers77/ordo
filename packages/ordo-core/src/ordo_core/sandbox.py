"""Ephemeral tenant clones: rehearse the destructive without the damage.

A sandbox is a full copy —structure and data— of a tenant schema under the
name `<tenant>_sb_<hex>`. The agent points `X-Ordo-Tenant` at it, tries what
it would not dare try in production and throws it away.

Cloning a schema is DDL and the application role has no DDL on purpose
(AGENTS.md §7), so every function here takes an *admin* session: a session
opened with the owner role, never the one serving normal requests.

Two invariants keep this module from becoming a foot-gun:

* a sandbox never clones another sandbox (no chains, no fan-out);
* only names carrying `_sb_` can be dropped, so a bug here cannot reach a
  production schema.
"""

from __future__ import annotations

import re
import secrets
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

from sqlalchemy import text

from ordo_core.errors import KernelError

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

SANDBOX_MARKER = "_sb_"
SCHEMA_PREFIX = "t_"
DEFAULT_TTL_HOURS = 24
MAX_TENANT_LEN = 60
MAX_IDENT_LEN = 63
APP_ROLE = "ordo_app"

# Postgres does not accept bound parameters for identifiers in DDL, so schema
# and table names are interpolated. This regex IS the defense against
# injection: nothing reaches a statement below without matching it first.
IDENT_RE = re.compile(r"^[a-z0-9_]+$")

REGISTRY_DDL = """
CREATE TABLE IF NOT EXISTS public.ir_sandbox (
    tenant text PRIMARY KEY,
    source_tenant text NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    expires_at timestamptz NOT NULL
)
"""

SCHEMA_EXISTS_SQL = "SELECT 1 FROM information_schema.schemata WHERE schema_name = :schema"

TABLES_SQL = (
    "SELECT table_name FROM information_schema.tables "
    "WHERE table_schema = :schema AND table_type = 'BASE TABLE' ORDER BY table_name"
)

SERIAL_TABLES_SQL = (
    "SELECT table_name FROM information_schema.columns "
    "WHERE table_schema = :schema AND column_name = 'id' "
    "AND data_type IN ('integer', 'bigint')"
)

# El CAST es para asyncpg: sin tipo declarado no puede inferir el del
# parámetro cuando solo aparece comparado consigo mismo.
LIST_SQL = (
    "SELECT tenant, source_tenant, created_at, expires_at FROM public.ir_sandbox "
    "WHERE (CAST(:source_tenant AS text) IS NULL OR source_tenant = CAST(:source_tenant AS text)) "
    "ORDER BY created_at, tenant"
)


class SandboxError(KernelError):
    """Sandbox lifecycle failure. Codes are public contract (AGENTS.md §5)."""


def is_sandbox(tenant: str) -> bool:
    """True when the tenant name carries the sandbox marker."""
    return SANDBOX_MARKER in tenant


def _ident(name: str) -> str:
    """Validates an identifier before it is interpolated into DDL."""
    if not IDENT_RE.match(name) or len(name) > MAX_IDENT_LEN:
        raise SandboxError(
            "SANDBOX_NAME_INVALID",
            f"Identificador inválido para un sandbox: {name!r}",
            hint="Solo minúsculas, dígitos y guion bajo, hasta 63 caracteres.",
        )
    return name


def _schema_for(tenant: str) -> str:
    return f"{SCHEMA_PREFIX}{_ident(tenant)}"


async def ensure_registry_table(admin_session: AsyncSession) -> None:
    """Creates `public.ir_sandbox` if it is not there yet (idempotent)."""
    await admin_session.execute(text(REGISTRY_DDL))
    await admin_session.commit()


async def _schema_exists(admin_session: AsyncSession, schema: str) -> bool:
    row = (await admin_session.execute(text(SCHEMA_EXISTS_SQL), {"schema": schema})).first()
    return row is not None


async def _table_names(admin_session: AsyncSession, schema: str) -> list[str]:
    rows = (await admin_session.execute(text(TABLES_SQL), {"schema": schema})).all()
    return [_ident(str(row.table_name)) for row in rows]


async def _serial_tables(admin_session: AsyncSession, schema: str) -> set[str]:
    """Tables whose `id` column is an integer, i.e. worth a sequence."""
    rows = (await admin_session.execute(text(SERIAL_TABLES_SQL), {"schema": schema})).all()
    return {str(row.table_name) for row in rows}


async def _clone_table(admin_session: AsyncSession, src: str, dst: str, table: str) -> None:
    """Copies structure and rows of one table into the sandbox schema.

    Identifiers were validated by `_ident` before reaching this statement.
    """
    await admin_session.execute(text(f'CREATE TABLE "{dst}"."{table}" AS TABLE "{src}"."{table}"'))


async def _make_writable(admin_session: AsyncSession, dst: str, table: str) -> None:
    """Gives the copied table its own sequence, default and primary key.

    `CREATE TABLE AS` copies neither constraints nor defaults nor sequences,
    so a raw copy is read-only in practice: any INSERT would fail on a null
    `id`. Identifiers are validated by `_ident` before interpolation.
    """
    sequence = f"{table}_id_seq"
    qualified = f'"{dst}"."{sequence}"'
    await admin_session.execute(text(f"CREATE SEQUENCE {qualified}"))
    max_id = (
        await admin_session.execute(
            text(f'SELECT COALESCE(MAX(id), 0) AS top FROM "{dst}"."{table}"')  # noqa: S608
        )
    ).scalar_one()
    await admin_session.execute(
        text("SELECT setval(:sequence, :next_id, false)"),
        {"sequence": qualified, "next_id": int(max_id) + 1},
    )
    await admin_session.execute(
        text(
            f'ALTER TABLE "{dst}"."{table}" '
            f"ALTER COLUMN id SET DEFAULT nextval('{qualified}'::regclass)"
        )
    )
    await admin_session.execute(text(f'ALTER TABLE "{dst}"."{table}" ADD PRIMARY KEY (id)'))


async def _grant_app_role(admin_session: AsyncSession, schema: str) -> None:
    """Data privileges for the application role: everything but DDL."""
    for statement in (
        f'GRANT USAGE ON SCHEMA "{schema}" TO {APP_ROLE}',
        f'GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA "{schema}" TO {APP_ROLE}',
        f'GRANT USAGE, SELECT, UPDATE ON ALL SEQUENCES IN SCHEMA "{schema}" TO {APP_ROLE}',
    ):
        await admin_session.execute(text(statement))


async def create_sandbox(
    admin_session: AsyncSession,
    source_tenant: str,
    *,
    ttl_hours: int = DEFAULT_TTL_HOURS,
    suffix: str | None = None,
) -> dict[str, Any]:
    """Clones a tenant schema into an ephemeral one and registers it.

    Returns the sandbox name, its source, when it expires and how many
    tables were copied.
    """
    if is_sandbox(source_tenant):
        raise SandboxError(
            "SANDBOX_NESTED",
            "Un sandbox no clona otro sandbox",
            hint="Clona desde el tenant original; los sandboxes no se encadenan.",
        )
    src_schema = _schema_for(source_tenant)
    if not await _schema_exists(admin_session, src_schema):
        raise SandboxError(
            "SANDBOX_SOURCE_NOT_FOUND",
            f"El tenant {source_tenant!r} no tiene schema que clonar.",
            hint="Crea el tenant antes de pedir un sandbox suyo.",
        )

    tenant = f"{source_tenant}{SANDBOX_MARKER}{suffix or secrets.token_hex(4)}"
    if len(tenant) > MAX_TENANT_LEN:
        raise SandboxError(
            "SANDBOX_NAME_INVALID",
            f"El nombre del sandbox excede {MAX_TENANT_LEN} caracteres: {tenant!r}",
            hint="Usa un sufijo más corto o un tenant de nombre más breve.",
        )
    dst_schema = _schema_for(tenant)

    tables = await _table_names(admin_session, src_schema)
    serial = await _serial_tables(admin_session, src_schema)
    await admin_session.execute(text(f'CREATE SCHEMA "{dst_schema}"'))
    for table in tables:
        await _clone_table(admin_session, src_schema, dst_schema, table)
        if table in serial:
            await _make_writable(admin_session, dst_schema, table)
    await _grant_app_role(admin_session, dst_schema)

    expires_at = datetime.now(UTC) + timedelta(hours=ttl_hours)
    await admin_session.execute(
        text(
            "INSERT INTO public.ir_sandbox (tenant, source_tenant, expires_at) "
            "VALUES (:tenant, :source_tenant, :expires_at)"
        ),
        {"tenant": tenant, "source_tenant": source_tenant, "expires_at": expires_at},
    )
    await admin_session.commit()
    return {
        "tenant": tenant,
        "source_tenant": source_tenant,
        "expires_at": expires_at,
        "tables": len(tables),
    }


async def drop_sandbox(admin_session: AsyncSession, tenant: str) -> None:
    """Drops a sandbox schema and forgets it.

    The marker check is the heart of this module's safety: a `DROP SCHEMA
    CASCADE` is irreversible, so the only names this function accepts are
    the ones it could have created itself. A bug upstream (a wrong tenant, a
    swapped variable) is refused here instead of erasing production.
    """
    if not is_sandbox(tenant):
        raise SandboxError(
            "SANDBOX_REFUSED",
            "Solo se borran schemas de sandbox",
            hint=f"El nombre debe contener {SANDBOX_MARKER!r}; un tenant real no se borra aquí.",
        )
    schema = _schema_for(tenant)
    await admin_session.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
    await admin_session.execute(
        text("DELETE FROM public.ir_sandbox WHERE tenant = :tenant"), {"tenant": tenant}
    )
    await admin_session.commit()


async def list_sandboxes(
    admin_session: AsyncSession, source_tenant: str | None = None
) -> list[dict[str, Any]]:
    """Registered sandboxes, optionally only those cloned from one tenant."""
    rows = (await admin_session.execute(text(LIST_SQL), {"source_tenant": source_tenant})).all()
    return [dict(row._mapping) for row in rows]


async def purge_expired(admin_session: AsyncSession) -> list[str]:
    """Drops every sandbox past its expiry. Returns the names it removed."""
    rows = (
        await admin_session.execute(
            text("SELECT tenant FROM public.ir_sandbox WHERE expires_at <= :now"),
            {"now": datetime.now(UTC)},
        )
    ).all()
    dropped: list[str] = []
    for row in rows:
        tenant = str(row.tenant)
        await drop_sandbox(admin_session, tenant)
        dropped.append(tenant)
    return dropped
