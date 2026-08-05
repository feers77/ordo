"""Crea el primer usuario IAM de un tenant y le asigna roles.

Uso:

    IAM_DATABASE_URL=postgresql+asyncpg://... \\
    uv run python tools/seed_iam_user.py demo duena@demo.cl \\
        --name "Dueña Demo" --roles ventas,contabilidad

Existe porque IAM **nunca auto-crea usuarios**: el bridge OIDC vincula el
primer login a un usuario pre-aprovisionado del mismo tenant y, si no lo
encuentra, se niega. Hasta ahora sembrar ese primer usuario exigía escribir
INSERTs a mano, así que ningún despliegue con enforcement era utilizable sin
tocar la base — y pedirle eso a quien despliega es pedirle que se equivoque.

No fija `idp_sub`: eso lo hace el bridge en el primer login verificado,
emparejando por correo. Sembrarlo aquí obligaría a conocer de antemano el
identificador que asigna el proveedor de identidad.

Idempotente: si el usuario ya existe se actualiza el nombre y se agregan las
membresías que falten, sin duplicar nada.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from pathlib import Path

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))


async def seed_user(
    session: AsyncSession,
    tenant: str,
    email: str,
    *,
    display_name: str,
    roles: list[str],
) -> dict[str, object]:
    from ordo_iam.models import Principal, PrincipalType, Role, RoleMember, User

    existing = await session.scalar(
        select(User).where(User.tenant == tenant, func.lower(User.email) == email.lower())
    )
    if existing is None:
        principal = Principal(
            type=PrincipalType.user,
            tenant=tenant,
            display_name=display_name,
        )
        session.add(principal)
        await session.flush()
        user = User(principal_id=principal.id, tenant=tenant, email=email, idp_sub=None)
        session.add(user)
        await session.flush()
        created = True
    else:
        user = existing
        principal = await session.get(Principal, user.principal_id)
        assert principal is not None
        principal.display_name = display_name
        created = False

    granted: list[str] = []
    missing: list[str] = []
    for name in roles:
        role = await session.scalar(select(Role).where(Role.tenant == tenant, Role.name == name))
        if role is None:
            # No se inventa el rol: los roles los declaran los `security.yaml`
            # de los módulos y los siembra `seed_iam_roles.py`. Crear uno vacío
            # aquí daría una membresía que no autoriza nada.
            missing.append(name)
            continue
        member = await session.get(RoleMember, (role.id, principal.id))
        if member is None:
            session.add(RoleMember(role_id=role.id, principal_id=principal.id))
            granted.append(name)
    await session.commit()
    return {
        "principal_id": str(principal.id),
        "created": created,
        "granted": granted,
        "missing_roles": missing,
        "linked": user.idp_sub is not None,
    }


async def main() -> None:
    parser = argparse.ArgumentParser(description="Crea el primer usuario IAM de un tenant.")
    parser.add_argument("tenant")
    parser.add_argument("email")
    parser.add_argument("--name", default="", help="Nombre visible; por defecto, el correo")
    parser.add_argument("--roles", default="", help="Roles separados por coma")
    args = parser.parse_args()

    url = os.environ.get("IAM_DATABASE_URL")
    if not url:
        raise SystemExit("IAM_DATABASE_URL requerida (base de datos del servicio IAM)")
    if "@" not in args.email:
        raise SystemExit("El segundo argumento es el correo del usuario.")

    roles = [name.strip() for name in args.roles.split(",") if name.strip()]
    engine = create_async_engine(url)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    async with maker() as session:
        result = await seed_user(
            session,
            args.tenant,
            args.email,
            display_name=args.name or args.email,
            roles=roles,
        )
    await engine.dispose()

    verb = "creado" if result["created"] else "ya existía"
    print(f"Usuario {args.email} {verb} en '{args.tenant}' (principal {result['principal_id']}).")
    if result["granted"]:
        print(f"  Roles agregados: {', '.join(result['granted'])}")
    if result["missing_roles"]:
        missing = ", ".join(result["missing_roles"])
        print(
            f"  Roles inexistentes, NO asignados: {missing}\n"
            f"  Siémbralos primero: uv run python tools/seed_iam_roles.py {args.tenant}"
        )
    if not result["linked"]:
        print(
            "  Falta el primer login: el bridge vincula el identificador del "
            "proveedor OIDC emparejando por correo verificado."
        )


if __name__ == "__main__":
    asyncio.run(main())
