"""Contabilización, reversión e inalterabilidad contra base real."""

from decimal import Decimal
from typing import Any

import pytest
from modules.account.services import AccountingError, AccountingService
from ordo_core.recordset import RecordSet
from sqlalchemy import text

pytestmark = pytest.mark.integration


def sale_lines(books: dict[str, Any], amount: str = "119000") -> list[dict[str, Any]]:
    return [
        {"account_id": books["clientes"], "debit": Decimal(amount), "name": "Cliente"},
        {"account_id": books["ventas"], "credit": Decimal(amount), "name": "Venta"},
    ]


async def make_move(books: dict[str, Any], **overrides: Any) -> int:
    service = AccountingService(books["env"])
    kwargs: dict[str, Any] = {
        "journal_id": books["journal_id"],
        "move_date": "2026-08-04",
        "currency_id": books["currency_id"],
        "company_id": books["company_id"],
        "lines": sale_lines(books),
    }
    kwargs.update(overrides)
    return await service.create_move(**kwargs)


class TestPosting:
    async def test_draft_move_has_no_number(self, books: dict[str, Any]) -> None:
        """El número se asigna al contabilizar, no al crear el borrador."""
        move_id = await make_move(books)
        moves = RecordSet(books["env"], "account.move")
        [move] = await moves.read([move_id], fields=["name", "state"])
        assert move["state"] == "draft"
        assert move["name"] is None

    async def test_posting_assigns_legal_number(self, books: dict[str, Any]) -> None:
        service = AccountingService(books["env"])
        move_id = await make_move(books)
        number = await service.action_post(move_id)
        assert number == "VTA/2026/00001"

        moves = RecordSet(books["env"], "account.move")
        [move] = await moves.read([move_id], fields=["name", "state"])
        assert move["state"] == "posted"
        assert move["name"] == number

    async def test_numbering_has_no_gaps(self, books: dict[str, Any]) -> None:
        """Requisito legal: la numeración de documentos no salta números."""
        service = AccountingService(books["env"])
        numbers = []
        for _ in range(3):
            move_id = await make_move(books)
            numbers.append(await service.action_post(move_id))
        assert numbers == ["VTA/2026/00001", "VTA/2026/00002", "VTA/2026/00003"]

    async def test_discarded_draft_consumes_no_number(self, books: dict[str, Any]) -> None:
        """Un borrador anulado no debe dejar un hueco en la numeración."""
        service = AccountingService(books["env"])
        discarded = await make_move(books)
        await service.action_cancel(discarded)

        posted = await make_move(books)
        assert await service.action_post(posted) == "VTA/2026/00001"

    async def test_unbalanced_move_cannot_be_created(self, books: dict[str, Any]) -> None:
        with pytest.raises(AccountingError) as exc:
            await make_move(
                books,
                lines=[
                    {"account_id": books["clientes"], "debit": Decimal("100")},
                    {"account_id": books["ventas"], "credit": Decimal("90")},
                ],
            )
        assert exc.value.code == "ACCOUNT_UNBALANCED"

    async def test_posting_twice_is_rejected(self, books: dict[str, Any]) -> None:
        service = AccountingService(books["env"])
        move_id = await make_move(books)
        await service.action_post(move_id)
        with pytest.raises(AccountingError) as exc:
            await service.action_post(move_id)
        assert exc.value.code == "ACCOUNT_ALREADY_POSTED"

    async def test_move_balance_matches(self, books: dict[str, Any]) -> None:
        service = AccountingService(books["env"])
        move_id = await make_move(books, lines=sale_lines(books, "250000"))
        debit, credit = await service.balance_of(move_id)
        assert debit == credit == Decimal("250000")


class TestImmutability:
    async def test_posted_move_cannot_be_cancelled(self, books: dict[str, Any]) -> None:
        """Contabilizado no se anula: se revierte. Es la regla de oro contable."""
        service = AccountingService(books["env"])
        move_id = await make_move(books)
        await service.action_post(move_id)
        with pytest.raises(AccountingError) as exc:
            await service.action_cancel(move_id)
        assert exc.value.code == "ACCOUNT_POSTED_IMMUTABLE"

    async def test_reversal_creates_mirror_entry(self, books: dict[str, Any]) -> None:
        service = AccountingService(books["env"])
        move_id = await make_move(books)
        await service.action_post(move_id)

        reversal_id = await service.action_reverse(move_id, reversal_date="2026-08-05")
        lines = RecordSet(books["env"], "account.move.line")
        original = await lines.search(
            [("move_id", "=", move_id)], fields=["account_id", "debit", "credit"]
        )
        reversed_rows = await lines.search(
            [("move_id", "=", reversal_id)], fields=["account_id", "debit", "credit"]
        )
        # cada debe del original es un haber en la reversión, y viceversa
        by_account = {r["account_id"]: r for r in reversed_rows["rows"]}
        for row in original["rows"]:
            mirror = by_account[row["account_id"]]
            assert mirror["debit"] == row["credit"]
            assert mirror["credit"] == row["debit"]

    async def test_original_survives_reversal(self, books: dict[str, Any]) -> None:
        service = AccountingService(books["env"])
        move_id = await make_move(books)
        number = await service.action_post(move_id)
        await service.action_reverse(move_id)

        moves = RecordSet(books["env"], "account.move")
        [original] = await moves.read([move_id], fields=["name", "state"])
        assert original["state"] == "posted"
        assert original["name"] == number  # intacto

    async def test_reversal_links_to_original(self, books: dict[str, Any]) -> None:
        service = AccountingService(books["env"])
        move_id = await make_move(books)
        await service.action_post(move_id)
        reversal_id = await service.action_reverse(move_id)

        moves = RecordSet(books["env"], "account.move")
        [reversal] = await moves.read([reversal_id], fields=["reversed_entry_id", "ref"])
        assert reversal["reversed_entry_id"] == move_id
        assert "Reversión" in reversal["ref"]

    async def test_draft_cannot_be_reversed(self, books: dict[str, Any]) -> None:
        service = AccountingService(books["env"])
        move_id = await make_move(books)
        with pytest.raises(AccountingError) as exc:
            await service.action_reverse(move_id)
        assert exc.value.code == "ACCOUNT_NOT_POSTED"

    async def test_net_effect_of_reversal_is_zero(self, books: dict[str, Any]) -> None:
        """Tras revertir, el saldo agregado de las dos piezas es cero."""
        service = AccountingService(books["env"])
        move_id = await make_move(books)
        await service.action_post(move_id)
        reversal_id = await service.action_reverse(move_id)
        await service.action_post(reversal_id)

        total = await books["session"].execute(
            text(
                "SELECT COALESCE(SUM(debit),0) - COALESCE(SUM(credit),0) AS neto "
                "FROM account_move_line WHERE move_id IN (:a, :b)"
            ),
            {"a": move_id, "b": reversal_id},
        )
        assert total.scalar() == Decimal("0")


class TestPeriods:
    async def test_closed_period_blocks_posting(self, books: dict[str, Any]) -> None:
        periods = RecordSet(books["env"], "account.period")
        await periods.create(
            [
                {
                    "name": "2026-08",
                    "date_from": "2026-08-01",
                    "date_to": "2026-08-31",
                    "state": "closed",
                    "company_id": books["company_id"],
                }
            ]
        )
        with pytest.raises(AccountingError) as exc:
            await make_move(books)
        assert exc.value.code == "ACCOUNT_PERIOD_LOCKED"
        assert "2026-08" in exc.value.message

    async def test_open_period_allows_posting(self, books: dict[str, Any]) -> None:
        periods = RecordSet(books["env"], "account.period")
        await periods.create(
            [
                {
                    "name": "2026-08",
                    "date_from": "2026-08-01",
                    "date_to": "2026-08-31",
                    "state": "open",
                    "company_id": books["company_id"],
                }
            ]
        )
        service = AccountingService(books["env"])
        move_id = await make_move(books)
        assert await service.action_post(move_id)

    async def test_date_outside_any_period_is_allowed(self, books: dict[str, Any]) -> None:
        """Sin período definido no se bloquea: cerrar es una decisión explícita."""
        move_id = await make_move(books, move_date="2027-01-15")
        assert move_id
