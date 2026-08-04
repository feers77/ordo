"""Instala en un tenant existente los módulos que le falten y ajusta grants.

Uso:

    POSTGRES_PASSWORD=... uv run python tools/upgrade_tenant.py demo

Idempotente: `ModuleInstaller.install` crea tablas con IF NOT EXISTS y el
grant de datos se re-aplica completo. No toca datos existentes.
"""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from ordo_core import Environment, Registry  # noqa: E402
from ordo_core.installer import ModuleInstaller  # noqa: E402
from ordo_core.modules import ModuleLoader  # noqa: E402


def admin_dsn() -> str:
    password = os.environ.get("POSTGRES_PASSWORD")
    if not password:
        raise SystemExit("POSTGRES_PASSWORD requerida (ver infra/compose/.env)")
    host = os.environ.get("ORDO_DB_HOST", "127.0.0.1")
    return f"postgresql+asyncpg://ordo:{password}@{host}:5432/ordo"


async def upgrade(session: AsyncSession, tenant: str) -> None:
    schema = f"t_{tenant}"
    exists = (
        await session.execute(
            text("SELECT 1 FROM information_schema.schemata WHERE schema_name = :s"),
            {"s": schema},
        )
    ).first()
    if not exists:
        raise SystemExit(f"El schema {schema} no existe; usa tools/seed_tenant.py.")

    loader = ModuleLoader([REPO_ROOT / "modules"])
    registry = Registry.build(loader.load())
    env = Environment(session=session, tenant=tenant, registry=registry, app_role=None)
    await env.bind()

    installer = ModuleInstaller(session, registry, loader.models_by_module)
    manifests = loader.discover()
    already = await installer.installed()
    order = loader.validate_graph(manifests)
    installed_now = []
    for name in order:
        if name in already:
            continue
        await installer.install(manifests[name])
        installed_now.append(name)
    await session.commit()

    for statement in (
        f'GRANT USAGE ON SCHEMA "{schema}" TO ordo_app',
        f'GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA "{schema}" TO ordo_app',
        f'GRANT USAGE, SELECT, UPDATE ON ALL SEQUENCES IN SCHEMA "{schema}" TO ordo_app',
    ):
        await session.execute(text(statement))
    await session.commit()

    if installed_now:
        print(f"Tenant '{tenant}': instalados {', '.join(installed_now)}.")
    else:
        print(f"Tenant '{tenant}': ya estaba al día.")


async def main() -> None:
    if len(sys.argv) != 2:
        raise SystemExit("Uso: uv run python tools/upgrade_tenant.py <tenant>")
    engine = create_async_engine(admin_dsn())
    maker = async_sessionmaker(engine, expire_on_commit=False)
    async with maker() as session:
        await upgrade(session, sys.argv[1])
    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
