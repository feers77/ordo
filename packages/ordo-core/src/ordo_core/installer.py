"""Per-tenant module installation and schema generation (design F2-07).

Tables come from the registry, so a simple module only declares models and
writes no DDL. Migrations exist for what a generator cannot infer: renames,
backfills, special indexes. Each module installs in its own transaction, so
a failing migration never leaves it half applied.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from ordo_core.errors import KernelError
from ordo_core.fields import Field
from ordo_core.modules import Manifest, migration_files
from ordo_core.registry import ModelDefinition, Registry

DDL = """
CREATE TABLE IF NOT EXISTS ir_module (
    name text PRIMARY KEY,
    version text NOT NULL,
    state text NOT NULL DEFAULT 'installed',
    installed_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS ir_module_migration (
    module text NOT NULL,
    filename text NOT NULL,
    applied_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (module, filename)
);
"""

COLUMN_TYPES = {
    "char": "text",
    "text": "text",
    "html": "text",
    "integer": "integer",
    "float": "double precision",
    "monetary": "numeric(18,2)",
    "boolean": "boolean",
    "date": "date",
    "datetime": "timestamptz",
    "binary": "bytea",
    "json": "jsonb",
    "selection": "text",
    "many2one": "integer",
}

TECHNICAL_DDL = {
    "id": "serial PRIMARY KEY",
    "create_uid": "integer",
    "create_date": "timestamptz NOT NULL DEFAULT now()",
    "write_uid": "integer",
    "write_date": "timestamptz NOT NULL DEFAULT now()",
    "version": "integer NOT NULL DEFAULT 1",
}


def _column_ddl(name: str, field: Field) -> str | None:
    if name in TECHNICAL_DDL:
        return f'"{name}" {TECHNICAL_DDL[name]}'
    if not field.store or field.field_type in {"one2many", "many2many"}:
        return None
    sql_type = COLUMN_TYPES.get(field.field_type)
    if sql_type is None:
        raise KernelError(
            "MODULE_UNSUPPORTED_FIELD_TYPE",
            f"No hay tipo SQL para '{field.field_type}' ({field.model_name}.{name})",
        )
    parts = [f'"{name}" {sql_type}']
    if field.required:
        parts.append("NOT NULL")
    return " ".join(parts)


def add_column_ddl(definition: ModelDefinition) -> list[str]:
    """ADD COLUMN IF NOT EXISTS for every stored field.

    A tenant installed before a module grew a field keeps its old table:
    without this, the column simply never appears and every query that
    mentions it fails at runtime. New columns are always added nullable —
    existing rows have no value for them and a NOT NULL would fail.
    """
    statements = []
    for name, field in definition.fields.items():
        ddl = _column_ddl(name, field)
        if ddl is None or name in TECHNICAL_DDL:
            continue
        column = ddl.removesuffix(" NOT NULL")
        statements.append(f'ALTER TABLE "{definition.table}" ADD COLUMN IF NOT EXISTS {column}')
    return statements


def index_ddl(definition: ModelDefinition) -> list[str]:
    """Indexes declared by the model's fields."""
    return [
        f'CREATE INDEX IF NOT EXISTS "ix_{definition.table}_{name}" '
        f'ON "{definition.table}" ("{name}")'
        for name, field in definition.fields.items()
        if field.index and field.store and name != "id"
    ]


def table_ddl(definition: ModelDefinition) -> list[str]:
    """CREATE TABLE plus the indexes declared by the model's fields."""
    columns = [
        ddl
        for name, field in definition.fields.items()
        if (ddl := _column_ddl(name, field)) is not None
    ]
    return [
        f'CREATE TABLE IF NOT EXISTS "{definition.table}" ({", ".join(columns)})',
        *index_ddl(definition),
    ]


class ModuleInstaller:
    def __init__(
        self,
        session: AsyncSession,
        registry: Registry,
        models_by_module: dict[str, list[str]] | None = None,
    ) -> None:
        self.session = session
        self.registry = registry
        self.models_by_module = models_by_module or {}

    async def prepare(self) -> None:
        for statement in filter(None, (s.strip() for s in DDL.split(";"))):
            await self.session.execute(text(statement))

    async def installed(self) -> dict[str, str]:
        rows = (await self.session.execute(text("SELECT name, version FROM ir_module"))).all()
        return {row.name: row.version for row in rows}

    async def create_tables(self, model_names: list[str] | None = None) -> list[str]:
        created = []
        for name in model_names or self.registry.model_names:
            definition = self.registry[name]
            # El orden importa: la tabla, después las columnas que pudieran
            # faltar (tenant instalado antes de que el módulo creciera), y
            # solo entonces los índices, que necesitan sus columnas.
            columns = [
                ddl
                for column, field in definition.fields.items()
                if (ddl := _column_ddl(column, field)) is not None
            ]
            await self.session.execute(
                text(f'CREATE TABLE IF NOT EXISTS "{definition.table}" ({", ".join(columns)})')
            )
            for statement in add_column_ddl(definition):
                await self.session.execute(text(statement))
            for statement in index_ddl(definition):
                await self.session.execute(text(statement))
            created.append(definition.table)
        return created

    async def install(
        self, manifest: Manifest, model_names: list[str] | None = None
    ) -> dict[str, Any]:
        """Install or upgrade one module. Atomic: all of it, or none of it.

        `model_names` defaults to every model the module declares, so nobody
        has to keep that list in sync by hand.
        """
        if model_names is None:
            model_names = self.models_by_module.get(manifest.name, [])
        await self.prepare()
        savepoint = await self.session.begin_nested()
        try:
            await self.create_tables(model_names)
            applied = await self._apply_migrations(manifest)
            await self.session.execute(
                text(
                    "INSERT INTO ir_module (name, version) VALUES (:name, :version) "
                    "ON CONFLICT (name) DO UPDATE SET version = EXCLUDED.version, "
                    "state = 'installed'"
                ),
                {"name": manifest.name, "version": manifest.version},
            )
        except Exception:
            await savepoint.rollback()
            raise
        await savepoint.commit()
        return {"module": manifest.name, "version": manifest.version, "migrations": applied}

    async def _apply_migrations(self, manifest: Manifest) -> list[str]:
        done = set(
            (
                await self.session.execute(
                    text("SELECT filename FROM ir_module_migration WHERE module = :module"),
                    {"module": manifest.name},
                )
            )
            .scalars()
            .all()
        )
        applied: list[str] = []
        for path in migration_files(manifest):
            if path.name in done:
                continue
            for statement in filter(None, (s.strip() for s in path.read_text().split(";"))):
                await self.session.execute(text(statement))
            await self.session.execute(
                text(
                    "INSERT INTO ir_module_migration (module, filename) VALUES (:module, :filename)"
                ),
                {"module": manifest.name, "filename": path.name},
            )
            applied.append(path.name)
        return applied
