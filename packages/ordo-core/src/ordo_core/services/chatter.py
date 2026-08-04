"""Chatter: messages, followers and activities (F2-06, PLAN §3.5).

This is the agent↔human channel. `author_kind` is mandatory so whoever
reads a thread can tell a person from an agent without inferring it.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from ordo_core.errors import KernelError

AUTHOR_KINDS = frozenset({"user", "agent", "system"})
MESSAGE_TYPES = frozenset({"comment", "notification", "tracking"})


class Chatter:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def post(
        self,
        *,
        model: str,
        res_id: int,
        body: str,
        author_kind: str,
        author_principal: str | None = None,
        message_type: str = "comment",
    ) -> int:
        if author_kind not in AUTHOR_KINDS:
            raise KernelError(
                "CHATTER_INVALID_AUTHOR_KIND",
                f"author_kind inválido: {author_kind!r}",
                hint=f"Valores permitidos: {sorted(AUTHOR_KINDS)}",
            )
        if message_type not in MESSAGE_TYPES:
            raise KernelError(
                "CHATTER_INVALID_MESSAGE_TYPE", f"message_type inválido: {message_type!r}"
            )
        row = (
            await self.session.execute(
                text(
                    "INSERT INTO mail_message (model, res_id, body, message_type, "
                    "author_principal, author_kind) VALUES (:model, :res_id, :body, "
                    ":message_type, :author, :kind) RETURNING id"
                ),
                {
                    "model": model,
                    "res_id": res_id,
                    "body": body,
                    "message_type": message_type,
                    "author": author_principal,
                    "kind": author_kind,
                },
            )
        ).first()
        assert row is not None
        return int(row.id)

    async def thread(self, model: str, res_id: int, limit: int = 50) -> list[dict[str, Any]]:
        rows = (
            await self.session.execute(
                text(
                    "SELECT id, body, message_type, author_principal, author_kind, create_date "
                    "FROM mail_message WHERE model = :model AND res_id = :res_id "
                    "ORDER BY id LIMIT :limit"
                ),
                {"model": model, "res_id": res_id, "limit": limit},
            )
        ).all()
        return [
            {
                "id": row.id,
                "body": row.body,
                "message_type": row.message_type,
                "author_principal": row.author_principal,
                "author_kind": row.author_kind,
                "create_date": row.create_date,
            }
            for row in rows
        ]

    async def track_changes(
        self,
        *,
        model: str,
        res_id: int,
        changes: dict[str, tuple[Any, Any]],
        author_kind: str = "system",
        author_principal: str | None = None,
    ) -> int | None:
        """Log old→new values for tracked fields; traceability is automatic."""
        if not changes:
            return None
        lines = [f"{field}: {old!r} → {new!r}" for field, (old, new) in sorted(changes.items())]
        return await self.post(
            model=model,
            res_id=res_id,
            body="\n".join(lines),
            author_kind=author_kind,
            author_principal=author_principal,
            message_type="tracking",
        )

    # -- seguidores -------------------------------------------------------

    async def follow(self, model: str, res_id: int, principal_id: str) -> None:
        await self.session.execute(
            text(
                "INSERT INTO mail_follower (model, res_id, principal_id) "
                "VALUES (:model, :res_id, :principal) ON CONFLICT DO NOTHING"
            ),
            {"model": model, "res_id": res_id, "principal": principal_id},
        )

    async def unfollow(self, model: str, res_id: int, principal_id: str) -> None:
        await self.session.execute(
            text(
                "DELETE FROM mail_follower WHERE model = :model AND res_id = :res_id "
                "AND principal_id = :principal"
            ),
            {"model": model, "res_id": res_id, "principal": principal_id},
        )

    async def followers(self, model: str, res_id: int) -> list[str]:
        rows = (
            await self.session.execute(
                text(
                    "SELECT principal_id FROM mail_follower WHERE model = :model "
                    "AND res_id = :res_id ORDER BY principal_id"
                ),
                {"model": model, "res_id": res_id},
            )
        ).scalars()
        return list(rows.all())

    # -- actividades ------------------------------------------------------

    async def schedule_activity(
        self,
        *,
        model: str,
        res_id: int,
        summary: str,
        assigned_to: str,
        date_deadline: date,
    ) -> int:
        row = (
            await self.session.execute(
                text(
                    "INSERT INTO mail_activity (model, res_id, summary, assigned_to, "
                    "date_deadline) VALUES (:model, :res_id, :summary, :assigned, :deadline) "
                    "RETURNING id"
                ),
                {
                    "model": model,
                    "res_id": res_id,
                    "summary": summary,
                    "assigned": assigned_to,
                    "deadline": date_deadline,
                },
            )
        ).first()
        assert row is not None
        return int(row.id)

    async def activities(self, model: str, res_id: int) -> list[dict[str, Any]]:
        rows = (
            await self.session.execute(
                text(
                    "SELECT id, summary, assigned_to, date_deadline, done FROM mail_activity "
                    "WHERE model = :model AND res_id = :res_id AND NOT done "
                    "ORDER BY date_deadline"
                ),
                {"model": model, "res_id": res_id},
            )
        ).all()
        today = datetime.now(UTC).date()
        return [
            {
                "id": row.id,
                "summary": row.summary,
                "assigned_to": row.assigned_to,
                "date_deadline": row.date_deadline,
                "state": _activity_state(row.date_deadline, today),
            }
            for row in rows
        ]

    async def complete_activity(self, activity_id: int) -> None:
        await self.session.execute(
            text("UPDATE mail_activity SET done = true WHERE id = :id"), {"id": activity_id}
        )


def _activity_state(deadline: date, today: date) -> str:
    if deadline < today:
        return "overdue"
    if deadline == today:
        return "today"
    return "planned"
