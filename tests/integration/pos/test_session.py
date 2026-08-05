"""El turno de caja contra la base real: abrir, cerrar y arquear."""

from decimal import Decimal
from typing import Any

import pytest
from modules.pos.services import PosError, PosSessionService
from ordo_core.actions import dispatch
from ordo_core.recordset import RecordSet
from ordo_core.reports import run_report

pytestmark = pytest.mark.integration


async def opened_session(shop: dict[str, Any], opening: str = "50000") -> int:
    service = PosSessionService(shop["env"])
    session_id = await service.create_session(config_id=shop["pos_config"])
    await dispatch(
        shop["env"],
        "pos.session",
        "action_open",
        session_id,
        {"opening_cash": opening},
    )
    return session_id


class TestOpening:
    async def test_opening_assigns_a_number_and_the_float(self, shop: dict[str, Any]) -> None:
        session_id = await opened_session(shop, "50000")
        [session] = await RecordSet(shop["env"], "pos.session").read(
            [session_id], fields=["name", "state", "opening_cash", "opened_at"]
        )
        assert session["name"] == "POS/00001"
        assert session["state"] == "opened"
        assert session["opening_cash"] == Decimal("50000.00")
        assert session["opened_at"] is not None
        assert session["opened_at"].tzinfo is not None  # UTC explícito

    async def test_a_second_open_shift_on_the_same_register_is_refused(
        self, shop: dict[str, Any]
    ) -> None:
        """Con dos turnos vivos sobre el mismo cajón, el arqueo no significa nada."""
        await opened_session(shop)
        service = PosSessionService(shop["env"])
        second = await service.create_session(config_id=shop["pos_config"])
        with pytest.raises(PosError) as excinfo:
            await dispatch(
                shop["env"], "pos.session", "action_open", second, {"opening_cash": "10000"}
            )
        assert excinfo.value.code == "POS_SESSION_ALREADY_OPEN"

    async def test_opening_twice_is_an_invalid_transition(self, shop: dict[str, Any]) -> None:
        session_id = await opened_session(shop)
        with pytest.raises(PosError) as excinfo:
            await dispatch(
                shop["env"], "pos.session", "action_open", session_id, {"opening_cash": "1"}
            )
        assert excinfo.value.code == "POS_SESSION_INVALID_TRANSITION"

    async def test_a_negative_float_is_refused(self, shop: dict[str, Any]) -> None:
        service = PosSessionService(shop["env"])
        session_id = await service.create_session(config_id=shop["pos_config"])
        with pytest.raises(PosError) as excinfo:
            await dispatch(
                shop["env"], "pos.session", "action_open", session_id, {"opening_cash": "-1"}
            )
        assert excinfo.value.code == "POS_OPENING_CASH_INVALID"

    async def test_an_unknown_register_is_refused(self, shop: dict[str, Any]) -> None:
        with pytest.raises(PosError) as excinfo:
            await PosSessionService(shop["env"]).create_session(config_id=99999)
        assert excinfo.value.code == "POS_CONFIG_MISSING"


class TestClosing:
    async def test_closing_without_difference_books_nothing(self, shop: dict[str, Any]) -> None:
        """Sin diferencia no hay asiento: una partida 0/0 no es contabilidad."""
        session_id = await opened_session(shop, "50000")
        await dispatch(shop["env"], "pos.session", "action_close_register", session_id, {})
        result = await dispatch(
            shop["env"],
            "pos.session",
            "action_close",
            session_id,
            {"counted_cash": "50000"},
        )
        assert result["difference"] == "0.00"
        assert result["move_id"] is None

        balance = await run_report(
            shop["env"], "account.trial_balance", {"company_id": shop["company_id"]}
        )
        assert balance["balanced"] is True

    async def test_a_shortfall_is_booked_as_a_loss(self, shop: dict[str, Any]) -> None:
        session_id = await opened_session(shop, "50000")
        await dispatch(shop["env"], "pos.session", "action_close_register", session_id, {})
        result = await dispatch(
            shop["env"],
            "pos.session",
            "action_close",
            session_id,
            {"counted_cash": "49500", "note": "vuelto de mas en el ultimo ticket"},
        )
        assert result["expected_cash"] == "50000.00"
        assert result["difference"] == "-500.00"
        assert result["move_id"] is not None

        lines = await RecordSet(shop["env"], "account.move.line").search(
            [("move_id", "=", result["move_id"])], fields=["account_id", "debit", "credit"]
        )
        by_account = {row["account_id"]: row for row in lines["rows"]}
        assert by_account[shop["diferencias"]]["debit"] == Decimal("500.00")
        assert by_account[shop["caja"]]["credit"] == Decimal("500.00")

        [move] = await RecordSet(shop["env"], "account.move").read(
            [result["move_id"]], fields=["state", "ref"]
        )
        assert move["state"] == "posted"  # un faltante en borrador nadie lo mira
        assert "Faltante" in move["ref"]

        balance = await run_report(
            shop["env"], "account.trial_balance", {"company_id": shop["company_id"]}
        )
        assert balance["balanced"] is True

    async def test_a_surplus_is_booked_the_other_way_round(self, shop: dict[str, Any]) -> None:
        session_id = await opened_session(shop, "50000")
        await dispatch(shop["env"], "pos.session", "action_close_register", session_id, {})
        result = await dispatch(
            shop["env"],
            "pos.session",
            "action_close",
            session_id,
            {"counted_cash": "50300"},
        )
        assert result["difference"] == "300.00"
        lines = await RecordSet(shop["env"], "account.move.line").search(
            [("move_id", "=", result["move_id"])], fields=["account_id", "debit", "credit"]
        )
        by_account = {row["account_id"]: row for row in lines["rows"]}
        assert by_account[shop["caja"]]["debit"] == Decimal("300.00")
        assert by_account[shop["diferencias"]]["credit"] == Decimal("300.00")

    async def test_withdrawals_lower_the_expected_cash(self, shop: dict[str, Any]) -> None:
        session_id = await opened_session(shop, "50000")
        await dispatch(shop["env"], "pos.session", "action_close_register", session_id, {})
        result = await dispatch(
            shop["env"],
            "pos.session",
            "action_close",
            session_id,
            {"counted_cash": "20000", "withdrawals": "30000"},
        )
        assert result["expected_cash"] == "20000.00"
        assert result["difference"] == "0.00"

    async def test_closing_needs_the_register_closed_first(self, shop: dict[str, Any]) -> None:
        session_id = await opened_session(shop)
        with pytest.raises(PosError) as excinfo:
            await dispatch(
                shop["env"],
                "pos.session",
                "action_close",
                session_id,
                {"counted_cash": "50000"},
            )
        assert excinfo.value.code == "POS_SESSION_INVALID_TRANSITION"

    async def test_closing_without_counting_is_not_an_audit(self, shop: dict[str, Any]) -> None:
        session_id = await opened_session(shop)
        await dispatch(shop["env"], "pos.session", "action_close_register", session_id, {})
        with pytest.raises(PosError) as excinfo:
            await dispatch(shop["env"], "pos.session", "action_close", session_id, {})
        assert excinfo.value.code == "POS_COUNTED_CASH_REQUIRED"

    async def test_a_closed_shift_is_immutable(self, shop: dict[str, Any]) -> None:
        session_id = await opened_session(shop)
        await dispatch(shop["env"], "pos.session", "action_close_register", session_id, {})
        await dispatch(
            shop["env"],
            "pos.session",
            "action_close",
            session_id,
            {"counted_cash": "50000"},
        )
        with pytest.raises(PosError) as excinfo:
            await dispatch(
                shop["env"],
                "pos.session",
                "action_close",
                session_id,
                {"counted_cash": "40000"},
            )
        assert excinfo.value.code == "POS_SESSION_INVALID_TRANSITION"

    async def test_closing_a_shift_frees_the_register(self, shop: dict[str, Any]) -> None:
        first = await opened_session(shop)
        await dispatch(shop["env"], "pos.session", "action_close_register", first, {})
        await dispatch(shop["env"], "pos.session", "action_close", first, {"counted_cash": "50000"})
        second = await opened_session(shop, "20000")
        [session] = await RecordSet(shop["env"], "pos.session").read(
            [second], fields=["name", "state"]
        )
        assert session["name"] == "POS/00002"
        assert session["state"] == "opened"


class TestSimulation:
    async def test_dry_run_does_not_burn_the_shift_number(self, shop: dict[str, Any]) -> None:
        """Simular un turno no puede consumir numeración: la secuencia es sin
        huecos y el hueco sería permanente."""
        service = PosSessionService(shop["env"])
        session_id = await service.create_session(config_id=shop["pos_config"])
        simulated = await dispatch(
            shop["env"],
            "pos.session",
            "action_open",
            session_id,
            {"opening_cash": "50000"},
            dry_run=True,
        )
        assert simulated["would_return"]["name"] == "POS/00001"

        [session] = await RecordSet(shop["env"], "pos.session").read(
            [session_id], fields=["name", "state"]
        )
        assert session["state"] == "draft"
        assert session["name"] is None

        real = await dispatch(
            shop["env"],
            "pos.session",
            "action_open",
            session_id,
            {"opening_cash": "50000"},
        )
        assert real["name"] == "POS/00001"  # el número no se gastó en la simulación

    async def test_closing_declares_that_it_needs_approval(self, shop: dict[str, Any]) -> None:
        """La diferencia de caja es la señal de robo hormiga: que la persona
        responsable la vea y la autorice es el control, no un trámite."""
        from ordo_core.actions import actions_for

        by_name = {spec.name: spec for spec in actions_for("pos.session")}
        assert by_name["action_close"].requires_approval is True
        assert by_name["action_open"].requires_approval is False
