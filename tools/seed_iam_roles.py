"""Carga los roles declarados por los módulos en la base IAM de un tenant.

Uso:

    IAM_DATABASE_URL=postgresql+asyncpg://... uv run python tools/seed_iam_roles.py demo

Idempotente: el rol se crea si falta y cada ACL se upserta al valor declarado
en los `security.yaml` de los módulos. La membresía (quién tiene cada rol) es
decisión del tenant y no se toca aquí.
"""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from ordo_core.security import load_security_specs  # noqa: E402


async def seed(session: AsyncSession, tenant: str) -> None:
    from ordo_iam.models import Acl, Role

    specs = load_security_specs(REPO_ROOT / "modules")
    created_roles = 0
    upserted = 0
    for spec in specs:
        role = await session.scalar(
            select(Role).where(Role.tenant == tenant, Role.name == spec.name)
        )
        if role is None:
            role = Role(tenant=tenant, name=spec.name)
            session.add(role)
            await session.flush()
            created_roles += 1
        for model, perms in spec.grants.items():
            acl = await session.scalar(
                select(Acl).where(Acl.role_id == role.id, Acl.model == model)
            )
            if acl is None:
                acl = Acl(role_id=role.id, model=model)
                session.add(acl)
            acl.perm_read = "read" in perms
            acl.perm_write = "write" in perms
            acl.perm_create = "create" in perms
            acl.perm_unlink = "unlink" in perms
            upserted += 1
    await session.commit()
    print(
        f"Tenant '{tenant}': {len(specs)} roles ({created_roles} nuevos), {upserted} ACLs al día."
    )


async def main() -> None:
    if len(sys.argv) != 2:
        raise SystemExit("Uso: uv run python tools/seed_iam_roles.py <tenant>")
    url = os.environ.get("IAM_DATABASE_URL")
    if not url:
        raise SystemExit("IAM_DATABASE_URL requerida")
    engine = create_async_engine(url)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    async with maker() as session:
        await seed(session, sys.argv[1])
    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
