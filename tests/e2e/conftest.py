"""Fixtures e2e: Keycloak real + servicio ordo-iam real sobre HTTP.

Requiere el stack levantado (`make up`). Se salta si Keycloak no responde.
"""

import os
import socket
import subprocess
import time
import uuid
from collections.abc import Iterator

import httpx
import pytest

KEYCLOAK_URL = os.environ.get("E2E_KEYCLOAK_URL", "http://127.0.0.1:8080")
REALM = "ordo"
ADMIN_USER = "admin"
IAM_ISSUER = "http://127.0.0.1:8099"


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def _admin_password() -> str:
    env_path = os.environ.get("E2E_ENV_FILE", "infra/compose/.env")
    try:
        for line in open(env_path):  # noqa: PTH123, SIM115
            if line.startswith("KEYCLOAK_ADMIN_PASSWORD="):
                return line.split("=", 1)[1].strip()
    except OSError:
        pass
    return os.environ.get("KEYCLOAK_ADMIN_PASSWORD", "")


@pytest.fixture(scope="session")
def keycloak_admin_token() -> str:
    try:
        resp = httpx.post(
            f"{KEYCLOAK_URL}/realms/master/protocol/openid-connect/token",
            data={
                "grant_type": "password",
                "client_id": "admin-cli",
                "username": ADMIN_USER,
                "password": _admin_password(),
            },
            timeout=10,
        )
    except httpx.HTTPError:
        pytest.skip("Keycloak no disponible (make up)")
    if resp.status_code != 200:
        pytest.skip(f"No se pudo autenticar en Keycloak: {resp.status_code}")
    return str(resp.json()["access_token"])


@pytest.fixture(scope="session")
def kc_user(keycloak_admin_token: str) -> tuple[str, str, str]:
    """Crea un usuario real en el realm ordo. Devuelve (email, password, tenant)."""
    email = f"e2e-{uuid.uuid4().hex[:8]}@acme.cl"
    password = uuid.uuid4().hex
    tenant = "acme"
    headers = {"Authorization": f"Bearer {keycloak_admin_token}"}
    resp = httpx.post(
        f"{KEYCLOAK_URL}/admin/realms/{REALM}/users",
        headers=headers,
        json={
            "username": email,
            "email": email,
            "emailVerified": True,
            "enabled": True,
            "firstName": "E2E",
            "lastName": "Test",
            "requiredActions": [],
            "attributes": {"tenant": tenant},
            "credentials": [{"type": "password", "value": password, "temporary": False}],
        },
        timeout=10,
    )
    if resp.status_code not in (201, 409):
        pytest.skip(f"No se pudo crear usuario en Keycloak: {resp.status_code} {resp.text}")
    return email, password, tenant


@pytest.fixture(scope="session")
def iam_service(e2e_db_url: str) -> Iterator[str]:
    """Levanta ordo-iam real contra Keycloak y una base efímera."""
    port = _free_port()
    env = {
        **os.environ,
        "IAM_DATABASE_URL": e2e_db_url,
        "OIDC_ISSUER": f"{KEYCLOAK_URL}/realms/{REALM}",
        "OIDC_AUDIENCE": "ordo-api",
        "IAM_ISSUER": IAM_ISSUER,
        "REDIS_URL": os.environ.get("REDIS_URL", "redis://127.0.0.1:6379/1"),
        "LOG_LEVEL": "WARNING",
    }
    proc = subprocess.Popen(
        [
            "uv",
            "run",
            "uvicorn",
            "ordo_iam.main:app",
            "--host",
            "127.0.0.1",
            "--port",
            str(port),
        ],
        env=env,
    )
    base_url = f"http://127.0.0.1:{port}"
    deadline = time.time() + 60
    while time.time() < deadline:
        try:
            if httpx.get(f"{base_url}/healthz", timeout=2).status_code == 200:
                break
        except httpx.HTTPError:
            time.sleep(0.5)
    else:
        proc.terminate()
        pytest.skip("ordo-iam no arrancó")
    yield base_url
    proc.terminate()
    proc.wait(timeout=10)


@pytest.fixture(scope="session")
def e2e_db_url() -> Iterator[str]:
    """Base efímera migrada para el servicio e2e."""
    import asyncio

    from sqlalchemy import text
    from sqlalchemy.ext.asyncio import create_async_engine

    pw = os.environ.get("POSTGRES_PASSWORD", "")
    admin_dsn = f"postgresql+asyncpg://ordo:{pw}@127.0.0.1:5432/ordo"
    db_name = f"ordo_iam_e2e_{uuid.uuid4().hex[:8]}"

    async def create() -> str:
        engine = create_async_engine(admin_dsn, isolation_level="AUTOCOMMIT")
        async with engine.connect() as conn:
            await conn.execute(text(f'CREATE DATABASE "{db_name}"'))
        await engine.dispose()
        url = f"postgresql+asyncpg://ordo:{pw}@127.0.0.1:5432/{db_name}"
        from ordo_iam.migrations import upgrade_to_head

        await upgrade_to_head(url)
        return url

    async def drop() -> None:
        engine = create_async_engine(admin_dsn, isolation_level="AUTOCOMMIT")
        async with engine.connect() as conn:
            await conn.execute(text(f'DROP DATABASE IF EXISTS "{db_name}" WITH (FORCE)'))
        await engine.dispose()

    try:
        url = asyncio.run(create())
    except Exception:
        pytest.skip("Postgres no disponible (make up)")
    yield url
    asyncio.run(drop())
