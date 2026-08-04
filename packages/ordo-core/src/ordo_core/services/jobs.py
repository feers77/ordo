"""Postgres-backed job queue and cron (F2-05, ADR-007).

Enqueueing is part of the business transaction: if the commit fails, the
job never existed. Claiming uses FOR UPDATE SKIP LOCKED so two workers
can never take the same job.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

MAX_BACKOFF = timedelta(hours=1)


class JobQueue:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def enqueue(
        self,
        name: str,
        payload: dict[str, Any] | None = None,
        *,
        priority: int = 100,
        run_at: datetime | None = None,
        max_attempts: int = 5,
    ) -> int:
        row = (
            await self.session.execute(
                text(
                    "INSERT INTO ir_job (name, payload, priority, run_at, max_attempts) "
                    "VALUES (:name, CAST(:payload AS jsonb), :priority, "
                    "COALESCE(:run_at, now()), :max_attempts) RETURNING id"
                ),
                {
                    "name": name,
                    "payload": json.dumps(payload or {}, default=str),
                    "priority": priority,
                    "run_at": run_at,
                    "max_attempts": max_attempts,
                },
            )
        ).first()
        assert row is not None
        return int(row.id)

    async def claim(self, worker: str, limit: int = 1) -> list[dict[str, Any]]:
        rows = (
            await self.session.execute(
                text(
                    "UPDATE ir_job SET state='running', locked_by=:worker, locked_at=now(), "
                    "attempts = attempts + 1 WHERE id IN ("
                    "  SELECT id FROM ir_job WHERE state='pending' AND run_at <= now() "
                    "  ORDER BY priority, run_at FOR UPDATE SKIP LOCKED LIMIT :limit"
                    ") RETURNING id, name, payload, attempts, max_attempts"
                ),
                {"worker": worker, "limit": limit},
            )
        ).all()
        return [
            {
                "id": row.id,
                "name": row.name,
                "payload": row.payload,
                "attempts": row.attempts,
                "max_attempts": row.max_attempts,
            }
            for row in rows
        ]

    async def complete(self, job_id: int) -> None:
        await self.session.execute(
            text("UPDATE ir_job SET state='done', locked_by=NULL WHERE id = :id"),
            {"id": job_id},
        )

    async def fail(self, job_id: int, error: str) -> str:
        """Reschedule with backoff, or move to the dead-letter state."""
        row = (
            await self.session.execute(
                text("SELECT attempts, max_attempts FROM ir_job WHERE id = :id FOR UPDATE"),
                {"id": job_id},
            )
        ).first()
        assert row is not None
        if row.attempts >= row.max_attempts:
            await self.session.execute(
                text(
                    "UPDATE ir_job SET state='dead', last_error=:error, locked_by=NULL "
                    "WHERE id = :id"
                ),
                {"id": job_id, "error": error},
            )
            return "dead"
        delay = min(timedelta(minutes=2**row.attempts), MAX_BACKOFF)
        await self.session.execute(
            text(
                "UPDATE ir_job SET state='pending', last_error=:error, locked_by=NULL, "
                "run_at=:run_at WHERE id = :id"
            ),
            {"id": job_id, "error": error, "run_at": datetime.now(UTC) + delay},
        )
        return "pending"

    # -- cron ------------------------------------------------------------

    async def schedule_cron(
        self,
        *,
        name: str,
        job_name: str,
        interval_seconds: int,
        payload: dict[str, Any] | None = None,
        next_call: datetime | None = None,
    ) -> None:
        await self.session.execute(
            text(
                "INSERT INTO ir_cron (name, job_name, payload, interval_seconds, next_call) "
                "VALUES (:name, :job_name, CAST(:payload AS jsonb), :interval, "
                "COALESCE(:next_call, now())) ON CONFLICT (name) DO NOTHING"
            ),
            {
                "name": name,
                "job_name": job_name,
                "payload": json.dumps(payload or {}, default=str),
                "interval": interval_seconds,
                "next_call": next_call,
            },
        )

    async def run_due_crons(self) -> list[int]:
        """Enqueue jobs for due crons; advance next_call before running."""
        rows = (
            await self.session.execute(
                text(
                    "UPDATE ir_cron SET next_call = now() + (interval_seconds || ' seconds')"
                    "::interval WHERE id IN ("
                    "  SELECT id FROM ir_cron WHERE active AND next_call <= now() "
                    "  FOR UPDATE SKIP LOCKED"
                    ") RETURNING job_name, payload"
                )
            )
        ).all()
        return [await self.enqueue(row.job_name, row.payload, priority=50) for row in rows]
