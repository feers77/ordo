"""Append-only audit log with per-tenant hash chaining (PLAN §2.7).

hash = sha256(prev_hash + canonical_json(event)). Tampering any row breaks
every hash after it.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from typing import Any

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from ordo_iam.models import AuditLog

GENESIS = "0" * 64


def _canonical(event: dict[str, Any]) -> str:
    return json.dumps(event, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _hash(prev_hash: str, event: dict[str, Any]) -> str:
    return hashlib.sha256((prev_hash + _canonical(event)).encode()).hexdigest()


async def append_audit(
    session: AsyncSession,
    *,
    tenant: str,
    event_type: str,
    payload: dict[str, Any],
    principal_id: uuid.UUID | None = None,
    act_chain: list[Any] | None = None,
    trace_id: str | None = None,
    token_jti: str | None = None,
) -> AuditLog:
    # serializa escritores concurrentes del mismo tenant
    await session.execute(
        text("SELECT pg_advisory_xact_lock(hashtext('iam_audit_' || :tenant))"),
        {"tenant": tenant},
    )
    prev = await session.scalar(
        select(AuditLog.hash).where(AuditLog.tenant == tenant).order_by(AuditLog.id.desc()).limit(1)
    )
    prev_hash = prev or GENESIS
    event = {
        "tenant": tenant,
        "event_type": event_type,
        "payload": payload,
        "principal_id": str(principal_id) if principal_id else None,
        "act_chain": act_chain or [],
        "trace_id": trace_id,
        "token_jti": token_jti,
    }
    row = AuditLog(
        tenant=tenant,
        principal_id=principal_id,
        act_chain=act_chain or [],
        event_type=event_type,
        payload=payload,
        trace_id=trace_id,
        token_jti=token_jti,
        prev_hash=prev_hash,
        hash=_hash(prev_hash, event),
    )
    session.add(row)
    await session.commit()
    return row


async def verify_chain(session: AsyncSession, tenant: str) -> tuple[bool, int | None]:
    """Recompute the chain; return (ok, first_broken_id)."""
    rows = (
        await session.scalars(
            select(AuditLog)
            .where(AuditLog.tenant == tenant)
            .order_by(AuditLog.id)
            .execution_options(populate_existing=True)
        )
    ).all()
    prev_hash = GENESIS
    for row in rows:
        event = {
            "tenant": row.tenant,
            "event_type": row.event_type,
            "payload": row.payload,
            "principal_id": str(row.principal_id) if row.principal_id else None,
            "act_chain": row.act_chain,
            "trace_id": row.trace_id,
            "token_jti": row.token_jti,
        }
        if row.prev_hash != prev_hash or row.hash != _hash(prev_hash, event):
            return False, row.id
        prev_hash = row.hash
    return True, None
