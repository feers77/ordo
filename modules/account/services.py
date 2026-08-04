"""Reglas contables: validación de asientos, contabilización y reversión.

Todo importe es `Decimal`. Un asiento contabilizado es inmutable: corregir
significa emitir una reversión, nunca editar el original.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any

from ordo_core.environment import Environment
from ordo_core.errors import KernelError
from ordo_core.recordset import RecordSet
from ordo_core.services.sequences import SequenceService

ZERO = Decimal("0")


class AccountingError(KernelError):
    """Error contable con código estable."""


def _money(value: Any) -> Decimal:
    if value is None:
        return ZERO
    if isinstance(value, Decimal):
        return value
    if isinstance(value, float):
        raise AccountingError(
            "ACCOUNT_FLOAT_AMOUNT",
            "Los importes contables no admiten float; usa Decimal o string decimal",
        )
    return Decimal(str(value))


def validate_lines(lines: list[dict[str, Any]]) -> Decimal:
    """Valida las partidas y devuelve el total del debe.

    Comprueba los invariantes que hacen que un asiento sea contable:
    ni debe ni haber negativos, nunca ambos en la misma línea, y la
    suma de ambos lados idéntica.
    """
    if not lines:
        raise AccountingError(
            "ACCOUNT_MOVE_EMPTY",
            "Un asiento debe tener al menos una partida",
            hint="Agrega las partidas antes de contabilizar.",
        )

    total_debit = ZERO
    total_credit = ZERO
    for index, line in enumerate(lines):
        debit = _money(line.get("debit"))
        credit = _money(line.get("credit"))

        if debit < ZERO or credit < ZERO:
            raise AccountingError(
                "ACCOUNT_NEGATIVE_AMOUNT",
                f"Partida {index}: el debe y el haber no pueden ser negativos",
                hint="Para invertir el sentido, cambia el importe al otro lado.",
            )
        if debit > ZERO and credit > ZERO:
            raise AccountingError(
                "ACCOUNT_LINE_BOTH_SIDES",
                f"Partida {index}: no puede tener debe y haber a la vez",
                hint="Divide la partida en dos si necesitas ambos movimientos.",
            )
        if debit == ZERO and credit == ZERO:
            raise AccountingError(
                "ACCOUNT_LINE_EMPTY",
                f"Partida {index}: debe o haber tiene que ser distinto de cero",
            )
        if not line.get("account_id"):
            raise AccountingError(
                "ACCOUNT_LINE_NO_ACCOUNT",
                f"Partida {index}: falta la cuenta contable",
            )
        total_debit += debit
        total_credit += credit

    if total_debit != total_credit:
        difference = total_debit - total_credit
        raise AccountingError(
            "ACCOUNT_UNBALANCED",
            f"El asiento no cuadra: debe {total_debit}, haber {total_credit}, "
            f"diferencia {difference}",
            hint="La suma del debe y la del haber deben ser idénticas.",
        )
    return total_debit


class AccountingService:
    def __init__(self, env: Environment) -> None:
        self.env = env
        self.moves = RecordSet(env, "account.move")
        self.lines = RecordSet(env, "account.move.line")
        self.sequences = SequenceService(env.session)

    async def create_move(
        self,
        *,
        journal_id: int,
        move_date: date | str,
        currency_id: int,
        company_id: int,
        lines: list[dict[str, Any]],
        ref: str | None = None,
        partner_id: int | None = None,
        narration: str | None = None,
    ) -> int:
        """Crea un asiento en borrador. Las partidas ya deben cuadrar."""
        total = validate_lines(lines)
        await self._check_period_open(move_date, company_id)

        [move_id] = await self.moves.create(
            [
                {
                    "journal_id": journal_id,
                    "date": move_date,
                    "currency_id": currency_id,
                    "company_id": company_id,
                    "ref": ref,
                    "partner_id": partner_id,
                    "narration": narration,
                    "amount_total": total,
                    "state": "draft",
                }
            ]
        )
        await self.lines.create(
            [
                {
                    "move_id": move_id,
                    "account_id": line["account_id"],
                    "name": line.get("name"),
                    "debit": _money(line.get("debit")),
                    "credit": _money(line.get("credit")),
                    "partner_id": line.get("partner_id"),
                    "date_maturity": line.get("date_maturity"),
                    "company_id": company_id,
                }
                for line in lines
            ]
        )
        return move_id

    async def action_post(self, move_id: int) -> str:
        """Contabiliza el asiento y le asigna su número legal.

        El número se toma al contabilizar, no al crear: así un borrador
        descartado no deja un hueco en la numeración.
        """
        move = await self._get_move(move_id)
        if move["state"] == "posted":
            raise AccountingError(
                "ACCOUNT_ALREADY_POSTED",
                f"El asiento {move.get('name') or move_id} ya está contabilizado",
            )
        if move["state"] == "cancel":
            raise AccountingError(
                "ACCOUNT_MOVE_CANCELLED",
                "Un asiento anulado no se puede contabilizar",
            )

        lines = await self._lines_of(move_id)
        validate_lines(lines)
        await self._check_period_open(move["date"], move["company_id"])

        journals = RecordSet(self.env, "account.journal")
        [journal] = await journals.read([move["journal_id"]], fields=["sequence_code"])
        number = await self.sequences.next_by_code(journal["sequence_code"])

        await self.moves.write([move_id], {"state": "posted", "name": number})
        return number

    async def action_cancel(self, move_id: int) -> None:
        move = await self._get_move(move_id)
        if move["state"] == "posted":
            raise AccountingError(
                "ACCOUNT_POSTED_IMMUTABLE",
                "Un asiento contabilizado no se anula: emite una reversión",
                hint="Usa action_reverse para revertir sus efectos.",
            )
        await self.moves.write([move_id], {"state": "cancel"})

    async def action_reverse(self, move_id: int, reversal_date: date | str | None = None) -> int:
        """Crea el asiento inverso. El original queda intacto (AGENTS.md §2.6)."""
        move = await self._get_move(move_id)
        if move["state"] != "posted":
            raise AccountingError(
                "ACCOUNT_NOT_POSTED",
                "Solo se revierte un asiento contabilizado",
                hint="Si está en borrador, anúlalo con action_cancel.",
            )
        lines = await self._lines_of(move_id)
        reversed_lines = [
            {
                "account_id": line["account_id"],
                "name": f"Reversión: {line.get('name') or ''}".strip(),
                "debit": _money(line.get("credit")),
                "credit": _money(line.get("debit")),
                "partner_id": line.get("partner_id"),
            }
            for line in lines
        ]
        reversal_id = await self.create_move(
            journal_id=move["journal_id"],
            move_date=reversal_date or move["date"],
            currency_id=move["currency_id"],
            company_id=move["company_id"],
            lines=reversed_lines,
            ref=f"Reversión de {move.get('name') or move_id}",
            partner_id=move.get("partner_id"),
        )
        await self.moves.write([reversal_id], {"reversed_entry_id": move_id})
        return reversal_id

    async def balance_of(self, move_id: int) -> tuple[Decimal, Decimal]:
        lines = await self._lines_of(move_id)
        return (
            sum((_money(line.get("debit")) for line in lines), ZERO),
            sum((_money(line.get("credit")) for line in lines), ZERO),
        )

    # -- internos ---------------------------------------------------------

    async def _get_move(self, move_id: int) -> dict[str, Any]:
        rows = await self.moves.read(
            [move_id],
            fields=[
                "id",
                "name",
                "state",
                "date",
                "journal_id",
                "currency_id",
                "company_id",
                "partner_id",
            ],
        )
        if not rows:
            raise AccountingError("ACCOUNT_MOVE_NOT_FOUND", f"No existe el asiento {move_id}")
        return rows[0]

    async def _lines_of(self, move_id: int) -> list[dict[str, Any]]:
        result = await self.lines.search(
            [("move_id", "=", move_id)],
            fields=["id", "account_id", "name", "debit", "credit", "partner_id"],
            limit=1000,
            active_test=False,
        )
        return result["rows"]

    async def _check_period_open(self, move_date: date | str, company_id: int) -> None:
        periods = RecordSet(self.env, "account.period")
        result = await periods.search(
            [
                ("company_id", "=", company_id),
                ("date_from", "<=", move_date),
                ("date_to", ">=", move_date),
            ],
            fields=["id", "name", "state"],
            active_test=False,
        )
        closed = [row for row in result["rows"] if row["state"] == "closed"]
        if closed:
            raise AccountingError(
                "ACCOUNT_PERIOD_LOCKED",
                f"El período {closed[0]['name']} está cerrado y no admite asientos",
                hint="Usa una fecha de un período abierto o solicita su reapertura.",
            )
