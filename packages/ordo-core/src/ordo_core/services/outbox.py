"""Transactional outbox (F2-05, ADR-008).

The event row is written in the same transaction as the business change,
so there is never an event without a commit nor a commit without an event.
The relay publishes and marks; republishing after a crash is safe because
the broker deduplicates by message id.
"""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

Publisher = Callable[[str, str, dict[str, Any]], Awaitable[None]]


class Outbox:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def emit(self, event_type: str, subject: str, payload: dict[str, Any]) -> int:
        row = (
            await self.session.execute(
                text(
                    "INSERT INTO ir_outbox (event_type, subject, payload) "
                    "VALUES (:event_type, :subject, CAST(:payload AS jsonb)) RETURNING id"
                ),
                {
                    "event_type": event_type,
                    "subject": subject,
                    "payload": json.dumps(payload, default=str),
                },
            )
        ).first()
        assert row is not None
        return int(row.id)

    async def pending(self, limit: int = 100) -> list[dict[str, Any]]:
        rows = (
            await self.session.execute(
                text(
                    "SELECT id, event_type, subject, payload FROM ir_outbox "
                    "WHERE published_at IS NULL ORDER BY id LIMIT :limit"
                ),
                {"limit": limit},
            )
        ).all()
        return [
            {
                "id": row.id,
                "event_type": row.event_type,
                "subject": row.subject,
                "payload": row.payload,
            }
            for row in rows
        ]

    async def relay(self, publish: Publisher, limit: int = 100) -> int:
        """Publish pending events and mark them. Returns how many were sent."""
        events = await self.pending(limit)
        published: list[int] = []
        for event in events:
            # message id = outbox id, so a republish after a crash is deduplicated
            await publish(event["subject"], str(event["id"]), event["payload"])
            published.append(int(event["id"]))
        if published:
            await self.session.execute(
                text("UPDATE ir_outbox SET published_at = now() WHERE id = ANY(:ids)"),
                {"ids": published},
            )
        return len(published)
