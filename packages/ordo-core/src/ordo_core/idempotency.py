"""Idempotency store for write operations (design F2-04, AGENTS.md §6).

Same key + same request → the stored response is replayed, never re-executed.
Same key + different request → explicit conflict, never a silent overwrite.
"""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from ordo_core.errors import KernelError

TTL = timedelta(hours=24)

DDL = """
CREATE TABLE IF NOT EXISTS ir_idempotency (
    key text PRIMARY KEY,
    request_hash text NOT NULL,
    response jsonb NOT NULL,
    create_date timestamptz NOT NULL DEFAULT now(),
    expires_at timestamptz NOT NULL
)
"""


def request_hash(payload: Any) -> str:
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(canonical.encode()).hexdigest()


async def create_table(session: AsyncSession) -> None:
    await session.execute(text(DDL))


async def replay(session: AsyncSession, key: str, payload: Any) -> dict[str, Any] | None:
    """Return the stored response, or None if this key is new."""
    row = (
        await session.execute(
            text(
                "SELECT request_hash, response FROM ir_idempotency "
                "WHERE key = :key AND expires_at > now()"
            ),
            {"key": key},
        )
    ).first()
    if row is None:
        return None
    if row.request_hash != request_hash(payload):
        raise KernelError(
            "IDEMPOTENCY_KEY_REUSED",
            "La Idempotency-Key ya se usó con un contenido distinto",
            hint="Usa una clave nueva para una operación distinta.",
        )
    return dict(row.response)


async def remember(session: AsyncSession, key: str, payload: Any, response: dict[str, Any]) -> None:
    await session.execute(
        text(
            "INSERT INTO ir_idempotency (key, request_hash, response, expires_at) "
            "VALUES (:key, :hash, CAST(:response AS jsonb), :expires) "
            "ON CONFLICT (key) DO NOTHING"
        ),
        {
            "key": key,
            "hash": request_hash(payload),
            "response": json.dumps(response, default=str),
            "expires": datetime.now(UTC) + TTL,
        },
    )
