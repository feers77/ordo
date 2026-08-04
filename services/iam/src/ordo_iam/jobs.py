"""Cola de trabajos de la base IAM (mismo contrato `ir_job` del kernel, ADR-007).

ordo-iam no depende de ordo-core (el kernel arrastra el ORM de negocio y el
Environment multi-tenant, que aquí no aplican), así que la tabla `ir_job` se
crea en la migración de IAM y esta clase habla el mismo dialecto que
`ordo_core.services.jobs.JobQueue`: encolar es parte de la transacción de
negocio y reclamar usa FOR UPDATE SKIP LOCKED.
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
        max_attempts: int = 5,
    ) -> int:
        row = (
            await self.session.execute(
                text(
                    "INSERT INTO ir_job (name, payload, priority, max_attempts) "
                    "VALUES (:name, CAST(:payload AS jsonb), :priority, :max_attempts) "
                    "RETURNING id"
                ),
                {
                    "name": name,
                    "payload": json.dumps(payload or {}, default=str),
                    "priority": priority,
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
        """Reprograma con backoff, o manda el job a la cola muerta."""
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
