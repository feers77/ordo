"""Liveness and readiness checks.

Readiness dependencies are declared via env var so every service uses
the same mechanism without code changes:

    READYZ_TCP_CHECKS="postgres=127.0.0.1:5432,redis=127.0.0.1:6379"
"""

from __future__ import annotations

import asyncio
import os

DEFAULT_TIMEOUT_S = 2.0


def parse_tcp_checks(raw: str | None = None) -> dict[str, tuple[str, int]]:
    raw = raw if raw is not None else os.environ.get("READYZ_TCP_CHECKS", "")
    checks: dict[str, tuple[str, int]] = {}
    for item in filter(None, (part.strip() for part in raw.split(","))):
        name, _, addr = item.partition("=")
        host, _, port = addr.rpartition(":")
        checks[name] = (host, int(port))
    return checks


async def check_tcp(host: str, port: int) -> bool:
    try:
        async with asyncio.timeout(DEFAULT_TIMEOUT_S):
            _, writer = await asyncio.open_connection(host, port)
    except (TimeoutError, OSError):
        return False
    writer.close()
    await writer.wait_closed()
    return True


async def run_readiness(checks: dict[str, tuple[str, int]]) -> dict[str, bool]:
    names = list(checks)
    results = await asyncio.gather(*(check_tcp(h, p) for h, p in checks.values()))
    return dict(zip(names, results, strict=True))
