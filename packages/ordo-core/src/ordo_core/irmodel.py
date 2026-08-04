"""Persist registry metadata into ir_model / ir_model_field (F2-01).

Makes the registry introspectable at runtime and gives studio-api and the
semantic schema a table to read from. Idempotent: upsert by name.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from ordo_core.registry import Registry

DDL = """
CREATE TABLE IF NOT EXISTS ir_model (
    id serial PRIMARY KEY,
    name text NOT NULL UNIQUE,
    description text NOT NULL,
    table_name text NOT NULL,
    inherits jsonb NOT NULL DEFAULT '{}'::jsonb,
    write_date timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS ir_model_field (
    id serial PRIMARY KEY,
    model text NOT NULL REFERENCES ir_model(name) ON DELETE CASCADE,
    name text NOT NULL,
    field_type text NOT NULL,
    spec jsonb NOT NULL,
    write_date timestamptz NOT NULL DEFAULT now(),
    UNIQUE (model, name)
);
"""


async def create_metadata_tables(session: AsyncSession) -> None:
    for statement in filter(None, (s.strip() for s in DDL.split(";"))):
        await session.execute(text(statement))


async def sync_registry(session: AsyncSession, registry: Registry) -> None:
    """Reflect the in-memory registry into the tenant's metadata tables."""
    await create_metadata_tables(session)
    for model_name in registry.model_names:
        definition = registry[model_name]
        await session.execute(
            text(
                "INSERT INTO ir_model (name, description, table_name, inherits) "
                "VALUES (:name, :description, :table, CAST(:inherits AS jsonb)) "
                "ON CONFLICT (name) DO UPDATE SET description = EXCLUDED.description, "
                "table_name = EXCLUDED.table_name, inherits = EXCLUDED.inherits, "
                "write_date = now()"
            ),
            {
                "name": definition.name,
                "description": definition.description,
                "table": definition.table,
                "inherits": _json(definition.inherits),
            },
        )
        for field_name, field_obj in definition.fields.items():
            await session.execute(
                text(
                    "INSERT INTO ir_model_field (model, name, field_type, spec) "
                    "VALUES (:model, :name, :field_type, CAST(:spec AS jsonb)) "
                    "ON CONFLICT (model, name) DO UPDATE SET "
                    "field_type = EXCLUDED.field_type, spec = EXCLUDED.spec, "
                    "write_date = now()"
                ),
                {
                    "model": definition.name,
                    "name": field_name,
                    "field_type": field_obj.field_type,
                    "spec": _json(field_obj.describe()),
                },
            )
    await session.commit()


def _json(value: Any) -> str:
    import json

    return json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)
