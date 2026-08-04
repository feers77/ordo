"""Alembic environment (async engine, runs in a worker thread)."""

from __future__ import annotations

import asyncio

from alembic import context
from sqlalchemy.ext.asyncio import create_async_engine

from ordo_iam.models import Base

target_metadata = Base.metadata


def do_run_migrations(connection) -> None:  # type: ignore[no-untyped-def]
    context.configure(connection=connection, target_metadata=target_metadata)
    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    url = context.config.get_main_option("sqlalchemy.url")
    assert url is not None
    engine = create_async_engine(url)
    async with engine.connect() as connection:
        await connection.run_sync(do_run_migrations)
        await connection.commit()
    await engine.dispose()


if context.is_offline_mode():
    msg = "Modo offline no soportado"
    raise RuntimeError(msg)

asyncio.run(run_async_migrations())
