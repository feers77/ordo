"""Fixtures de integración del kernel: base efímera con dos tenants."""

import os
import uuid
from collections.abc import AsyncIterator

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine


def _admin_dsn() -> str:
    pw = os.environ.get("POSTGRES_PASSWORD", "")
    return f"postgresql+asyncpg://ordo:{pw}@127.0.0.1:5432/ordo"


@pytest.fixture(scope="session")
async def core_db_url() -> AsyncIterator[str]:
    db_name = f"ordo_core_test_{uuid.uuid4().hex[:8]}"
    admin = create_async_engine(_admin_dsn(), isolation_level="AUTOCOMMIT")
    try:
        async with admin.connect() as conn:
            await conn.execute(text(f'CREATE DATABASE "{db_name}"'))
    except Exception:
        pytest.skip("Postgres no disponible (make up)")
    yield _admin_dsn().rsplit("/", 1)[0] + f"/{db_name}"
    async with admin.connect() as conn:
        await conn.execute(text(f'DROP DATABASE IF EXISTS "{db_name}" WITH (FORCE)'))
    await admin.dispose()


@pytest.fixture(scope="session")
async def app_role_ready(core_db_url: str) -> AsyncIterator[None]:
    """Rol de aplicación sin BYPASSRLS (espejo de infra/compose/postgres/init-app-role.sh)."""
    engine = create_async_engine(core_db_url, isolation_level="AUTOCOMMIT")
    async with engine.connect() as conn:
        await conn.execute(
            text(
                "DO $$ BEGIN IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname='ordo_app') "
                "THEN CREATE ROLE ordo_app LOGIN PASSWORD 'test' "
                "NOSUPERUSER NOCREATEDB NOCREATEROLE NOBYPASSRLS; END IF; END $$;"
            )
        )
        await conn.execute(text("GRANT ordo_app TO ordo"))
    await engine.dispose()
    yield


@pytest.fixture
async def core_session(core_db_url: str, app_role_ready: None) -> AsyncIterator[AsyncSession]:
    engine = create_async_engine(core_db_url)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    async with maker() as session:
        yield session
    await engine.dispose()
