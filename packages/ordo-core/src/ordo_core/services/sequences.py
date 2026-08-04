"""Sequence service (F2-05).

`no_gap` locks the sequence row, so concurrent transactions serialize and
legal documents never skip a number. Ranges are never cached.
"""

from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from ordo_core.errors import KernelError

NO_GAP = "no_gap"
STANDARD = "standard"


class SequenceService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def create(
        self,
        *,
        code: str,
        name: str,
        prefix: str = "",
        suffix: str = "",
        padding: int = 5,
        start: int = 1,
        implementation: str = STANDARD,
    ) -> None:
        if implementation not in {NO_GAP, STANDARD}:
            raise KernelError(
                "SEQUENCE_INVALID_IMPLEMENTATION",
                f"Implementación desconocida: {implementation!r}",
                hint=f"Usa '{STANDARD}' o '{NO_GAP}'.",
            )
        await self.session.execute(
            text(
                "INSERT INTO ir_sequence (code, name, prefix, suffix, padding, "
                "next_number, implementation) VALUES (:code, :name, :prefix, :suffix, "
                ":padding, :start, :implementation) ON CONFLICT (code) DO NOTHING"
            ),
            {
                "code": code,
                "name": name,
                "prefix": prefix,
                "suffix": suffix,
                "padding": padding,
                "start": start,
                "implementation": implementation,
            },
        )

    async def next_by_code(self, code: str) -> str:
        row = (
            await self.session.execute(
                text(
                    "SELECT id, prefix, suffix, padding, next_number, step, implementation "
                    "FROM ir_sequence WHERE code = :code FOR UPDATE"
                ),
                {"code": code},
            )
        ).first()
        if row is None:
            raise KernelError(
                "SEQUENCE_NOT_FOUND",
                f"No existe la secuencia '{code}'",
                hint="Créala antes de emitir documentos con ese código.",
            )
        number = int(row.next_number)
        await self.session.execute(
            text("UPDATE ir_sequence SET next_number = next_number + step WHERE id = :id"),
            {"id": row.id},
        )
        return f"{row.prefix}{number:0{row.padding}d}{row.suffix}"
