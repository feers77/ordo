"""Attachments with content-addressed deduplication (F2-06).

Metadata lives in Postgres, bytes in object storage. Size, checksum and
mimetype are derived from the content, never trusted from the client.
Two identical files share one object; deleting one keeps the other intact.
"""

from __future__ import annotations

import hashlib
from typing import Any, Protocol

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from ordo_core.errors import KernelError


class AttachmentStorage(Protocol):
    async def put(self, key: str, data: bytes) -> None: ...
    async def get(self, key: str) -> bytes: ...
    async def delete(self, key: str) -> None: ...


class InMemoryStorage:
    """For tests and local dev; MinIO implements the same protocol."""

    def __init__(self) -> None:
        self.objects: dict[str, bytes] = {}

    async def put(self, key: str, data: bytes) -> None:
        self.objects[key] = data

    async def get(self, key: str) -> bytes:
        if key not in self.objects:
            msg = f"objeto inexistente: {key}"
            raise KeyError(msg)
        return self.objects[key]

    async def delete(self, key: str) -> None:
        self.objects.pop(key, None)


class AttachmentService:
    def __init__(self, session: AsyncSession, storage: AttachmentStorage) -> None:
        self.session = session
        self.storage = storage

    async def upload(
        self,
        *,
        name: str,
        data: bytes,
        mimetype: str = "application/octet-stream",
        model: str | None = None,
        res_id: int | None = None,
    ) -> dict[str, Any]:
        checksum = hashlib.sha256(data).hexdigest()
        storage_key = f"sha256/{checksum}"
        existing = await self.session.scalar(
            text("SELECT count(*) FROM ir_attachment WHERE checksum = :checksum"),
            {"checksum": checksum},
        )
        if not existing:
            await self.storage.put(storage_key, data)
        row = (
            await self.session.execute(
                text(
                    "INSERT INTO ir_attachment (name, model, res_id, mimetype, file_size, "
                    "checksum, storage_key) VALUES (:name, :model, :res_id, :mimetype, "
                    ":size, :checksum, :key) RETURNING id"
                ),
                {
                    "name": name,
                    "model": model,
                    "res_id": res_id,
                    "mimetype": mimetype,
                    "size": len(data),
                    "checksum": checksum,
                    "key": storage_key,
                },
            )
        ).first()
        assert row is not None
        return {
            "id": int(row.id),
            "checksum": checksum,
            "file_size": len(data),
            "deduplicated": bool(existing),
        }

    async def download(self, attachment_id: int) -> bytes:
        row = (
            await self.session.execute(
                text("SELECT storage_key FROM ir_attachment WHERE id = :id"),
                {"id": attachment_id},
            )
        ).first()
        if row is None:
            raise KernelError("ATTACHMENT_NOT_FOUND", f"No existe el adjunto {attachment_id}")
        return await self.storage.get(row.storage_key)

    async def delete(self, attachment_id: int) -> None:
        """Drop the row; only remove the object when nothing else points at it."""
        row = (
            await self.session.execute(
                text("SELECT checksum, storage_key FROM ir_attachment WHERE id = :id"),
                {"id": attachment_id},
            )
        ).first()
        if row is None:
            raise KernelError("ATTACHMENT_NOT_FOUND", f"No existe el adjunto {attachment_id}")
        await self.session.execute(
            text("DELETE FROM ir_attachment WHERE id = :id"), {"id": attachment_id}
        )
        remaining = await self.session.scalar(
            text("SELECT count(*) FROM ir_attachment WHERE checksum = :checksum"),
            {"checksum": row.checksum},
        )
        if not remaining:
            await self.storage.delete(row.storage_key)

    async def for_record(self, model: str, res_id: int) -> list[dict[str, Any]]:
        rows = (
            await self.session.execute(
                text(
                    "SELECT id, name, mimetype, file_size, checksum FROM ir_attachment "
                    "WHERE model = :model AND res_id = :res_id ORDER BY id"
                ),
                {"model": model, "res_id": res_id},
            )
        ).all()
        return [
            {
                "id": row.id,
                "name": row.name,
                "mimetype": row.mimetype,
                "file_size": row.file_size,
                "checksum": row.checksum,
            }
            for row in rows
        ]
