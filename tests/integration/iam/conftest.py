"""Fixtures de integración IAM: base de datos real, schema desde Alembic."""

import os
import uuid
from collections.abc import AsyncIterator

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

ADMIN_DSN = os.environ.get(
    "IAM_TEST_ADMIN_DSN",
    "postgresql+asyncpg://ordo:{pw}@127.0.0.1:5432/ordo",
)


def _admin_dsn() -> str:
    pw = os.environ.get("POSTGRES_PASSWORD", "")
    return ADMIN_DSN.format(pw=pw)


@pytest.fixture(scope="session")
def test_db_name() -> str:
    return f"ordo_iam_test_{uuid.uuid4().hex[:8]}"


@pytest.fixture(scope="session")
async def test_db_url(test_db_name: str) -> AsyncIterator[str]:
    admin = create_async_engine(_admin_dsn(), isolation_level="AUTOCOMMIT")
    try:
        async with admin.connect() as conn:
            from sqlalchemy import text

            await conn.execute(text(f'CREATE DATABASE "{test_db_name}"'))
    except Exception:
        pytest.skip("Postgres no disponible (levanta el stack: make up)")
    url = _admin_dsn().rsplit("/", 1)[0] + f"/{test_db_name}"
    yield url
    async with admin.connect() as conn:
        from sqlalchemy import text

        await conn.execute(text(f'DROP DATABASE IF EXISTS "{test_db_name}" WITH (FORCE)'))
    await admin.dispose()


@pytest.fixture(scope="session")
async def migrated_db(test_db_url: str) -> str:
    from ordo_iam.migrations import upgrade_to_head

    await upgrade_to_head(test_db_url)
    return test_db_url


@pytest.fixture
async def session(migrated_db: str) -> AsyncIterator[AsyncSession]:
    engine = create_async_engine(migrated_db)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    async with maker() as s:
        yield s
    await engine.dispose()
