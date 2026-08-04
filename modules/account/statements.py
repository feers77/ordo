"""Extractos bancarios: lo que dice el banco contra lo que dicen los libros.

El emparejamiento automático es deliberadamente conservador: solo cuando hay
exactamente un candidato con el mismo importe. Ante ambigüedad no adivina;
deja la línea sin emparejar para que decida una persona o un agente con más
contexto.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from ordo_core.environment import Environment
from ordo_core.recordset import RecordSet

from modules.account.services import AccountingError

ZERO = Decimal("0")


class StatementError(AccountingError):
    """Error de extractos con código estable."""


class StatementService:
    def __init__(self, env: Environment) -> None:
        self.env = env
        self.statements = RecordSet(env, "account.bank.statement")
        self.lines = RecordSet(env, "account.bank.statement.line")
        self.move_lines = RecordSet(env, "account.move.line")

    async def create_statement(
        self,
        *,
        name: str,
        journal_id: int,
        date: str,
        balance_start: Decimal | str,
        balance_end: Decimal | str,
        company_id: int,
        lines: list[dict[str, Any]],
    ) -> int:
        if not lines:
            raise StatementError("STATEMENT_EMPTY", "Un extracto sin movimientos no concilia nada")
        journals = RecordSet(self.env, "account.journal")
        [journal] = await journals.read([journal_id], fields=["journal_type"])
        if journal["journal_type"] != "bank":
            raise StatementError(
                "STATEMENT_JOURNAL_INVALID",
                "El extracto pertenece a un diario de banco",
            )
        [statement_id] = await self.statements.create(
            [
                {
                    "name": name,
                    "journal_id": journal_id,
                    "date": date,
                    "balance_start": Decimal(str(balance_start)),
                    "balance_end": Decimal(str(balance_end)),
                    "company_id": company_id,
                    "state": "open",
                }
            ]
        )
        await self.lines.create(
            [
                {
                    "statement_id": statement_id,
                    "date": line["date"],
                    "amount": Decimal(str(line["amount"])),
                    "ref": line.get("ref"),
                    "partner_id": line.get("partner_id"),
                    "company_id": company_id,
                }
                for line in lines
            ]
        )
        return statement_id

    async def auto_match(self, statement_id: int) -> dict[str, int]:
        """Empareja líneas contra partidas del banco por importe exacto y único."""
        statement = await self._get(statement_id)
        self._expect_open(statement)
        bank_account = await self._bank_account(statement["journal_id"])

        result = await self.lines.search(
            [("statement_id", "=", statement_id), ("matched_move_line_id", "=", None)],
            fields=["id", "amount"],
            limit=1000,
        )
        matched = 0
        unmatched = 0
        for line in result["rows"]:
            candidate = await self._single_candidate(bank_account, line["amount"])
            if candidate is None:
                unmatched += 1
                continue
            await self.lines.write([line["id"]], {"matched_move_line_id": candidate})
            matched += 1
        return {"matched": matched, "unmatched": unmatched}

    async def match_line(self, line_id: int, move_line_id: int) -> None:
        """Emparejamiento manual; el importe tiene que coincidir exactamente."""
        [line] = await self.lines.read(
            [line_id], fields=["id", "statement_id", "amount", "matched_move_line_id"]
        )
        statement = await self._get(line["statement_id"])
        self._expect_open(statement)
        bank_account = await self._bank_account(statement["journal_id"])
        [move_line] = await self.move_lines.read(
            [move_line_id], fields=["id", "account_id", "debit", "credit"]
        )
        if move_line["account_id"] != bank_account:
            raise StatementError(
                "STATEMENT_WRONG_ACCOUNT",
                "La partida no pertenece a la cuenta de banco del extracto",
            )
        balance = move_line["debit"] - move_line["credit"]
        if balance != line["amount"]:
            raise StatementError(
                "STATEMENT_AMOUNT_MISMATCH",
                f"El movimiento es {line['amount']} y la partida {balance}",
                hint="Solo se emparejan importes idénticos.",
            )
        if await self._already_used(move_line_id):
            raise StatementError(
                "STATEMENT_LINE_ALREADY_USED",
                "Esa partida ya está emparejada con otro movimiento",
            )
        await self.lines.write([line_id], {"matched_move_line_id": move_line_id})

    async def action_validate(self, statement_id: int) -> None:
        """Cierra el extracto: cuadrado contra los saldos y todo emparejado."""
        statement = await self._get(statement_id)
        self._expect_open(statement)
        result = await self.lines.search(
            [("statement_id", "=", statement_id)],
            fields=["id", "amount", "matched_move_line_id"],
            limit=1000,
        )
        rows = result["rows"]
        pending = [row["id"] for row in rows if row["matched_move_line_id"] is None]
        if pending:
            raise StatementError(
                "STATEMENT_UNMATCHED",
                f"Quedan {len(pending)} movimientos sin emparejar",
                hint="Usa auto_match o match_line antes de validar.",
            )
        total = sum((row["amount"] for row in rows), ZERO)
        if statement["balance_start"] + total != statement["balance_end"]:
            difference = statement["balance_start"] + total - statement["balance_end"]
            raise StatementError(
                "STATEMENT_UNBALANCED",
                f"El extracto no cuadra: sobra o falta {difference}",
                hint="Revisa los saldos inicial y final o los movimientos.",
            )
        await self.statements.write([statement_id], {"state": "validated"})

    # -- internos ---------------------------------------------------------

    def _expect_open(self, statement: dict[str, Any]) -> None:
        if statement["state"] != "open":
            raise StatementError(
                "STATEMENT_VALIDATED_IMMUTABLE",
                "Un extracto validado no se modifica",
            )

    async def _get(self, statement_id: int) -> dict[str, Any]:
        rows = await self.statements.read(
            [statement_id],
            fields=["id", "state", "journal_id", "balance_start", "balance_end"],
        )
        if not rows:
            raise StatementError("STATEMENT_NOT_FOUND", f"No existe el extracto {statement_id}")
        return rows[0]

    async def _bank_account(self, journal_id: int) -> int:
        journals = RecordSet(self.env, "account.journal")
        [journal] = await journals.read([journal_id], fields=["default_account_id"])
        if journal["default_account_id"] is None:
            raise StatementError(
                "STATEMENT_JOURNAL_NO_ACCOUNT",
                "El diario de banco no tiene cuenta configurada",
            )
        return int(journal["default_account_id"])

    async def _single_candidate(self, bank_account: int, amount: Decimal) -> int | None:
        result = await self.move_lines.search(
            [
                ("account_id", "=", bank_account),
                ("move_id.state", "=", "posted"),
            ],
            fields=["id", "debit", "credit"],
            limit=500,
        )
        candidates = [
            row["id"]
            for row in result["rows"]
            if row["debit"] - row["credit"] == amount and not await self._already_used(row["id"])
        ]
        return candidates[0] if len(candidates) == 1 else None

    async def _already_used(self, move_line_id: int) -> bool:
        result = await self.lines.search(
            [("matched_move_line_id", "=", move_line_id)], fields=["id"], limit=1
        )
        return bool(result["rows"])
