"""Ciclo de vida del turno de caja: abrir, cerrar a ventas y arquear.

Cerrar es la operación delicada. El arqueo compara lo contado contra lo que
debería haber, y la diferencia se asienta en el acto: no existe el turno
"cerrado" cuyo faltante nadie registró, que es exactamente donde el robo
hormiga se vuelve invisible.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from ordo_core.environment import Environment
from ordo_core.recordset import RecordSet
from ordo_core.services.sequences import SequenceService

from modules.account.services import AccountingService
from modules.pos.cash import ZERO, CashError, difference, expected_cash, money

SEQUENCE_CODE = "pos.session"

CONFIG_FIELDS = (
    "id",
    "name",
    "location_id",
    "journal_id",
    "cash_journal_id",
    "cash_account_id",
    "difference_account_id",
    "document_type_code",
    "anonymous_partner_id",
    "price_includes_tax",
    "currency_id",
    "company_id",
)


class PosError(CashError):
    """Error del punto de venta con código estable."""


class PosSessionService:
    def __init__(self, env: Environment) -> None:
        self.env = env
        self.sessions = RecordSet(env, "pos.session")
        self.configs = RecordSet(env, "pos.config")
        self.accounting = AccountingService(env)

    # ---------------------------------------------------------------- lectura

    async def config(self, config_id: int) -> dict[str, Any]:
        rows = await self.configs.read([config_id], fields=list(CONFIG_FIELDS))
        if not rows:
            raise PosError(
                "POS_CONFIG_MISSING",
                f"No existe la caja {config_id}",
                hint="Crea la pos.config con su ubicación, diarios y cuentas antes de vender.",
            )
        return rows[0]

    async def _session(self, session_id: int) -> dict[str, Any]:
        rows = await self.sessions.read(
            [session_id],
            fields=[
                "id",
                "name",
                "state",
                "config_id",
                "opening_cash",
                "withdrawals",
                "company_id",
            ],
        )
        if not rows:
            raise PosError(
                "POS_SESSION_NOT_FOUND",
                f"No existe el turno {session_id}",
                hint="Revisa el id contra pos.session.",
            )
        return rows[0]

    def _expect(self, session: dict[str, Any], states: tuple[str, ...], verb: str) -> None:
        if session["state"] not in states:
            raise PosError(
                "POS_SESSION_INVALID_TRANSITION",
                (
                    f"Solo se puede {verb} un turno en estado {' o '.join(states)}; "
                    f"este está en {session['state']}"
                ),
                hint="Consulta las acciones disponibles con explain sobre el turno.",
            )

    # --------------------------------------------------------------- escritura

    async def create_session(self, *, config_id: int) -> int:
        config = await self.config(config_id)
        [session_id] = await self.sessions.create(
            [
                {
                    "name": None,
                    "config_id": config_id,
                    "state": "draft",
                    "opened_at": None,
                    "closed_at": None,
                    "opening_cash": ZERO,
                    "counted_cash": None,
                    "expected_cash": None,
                    "difference": None,
                    "withdrawals": ZERO,
                    "move_id": None,
                    "note": None,
                    "company_id": config["company_id"],
                }
            ]
        )
        return session_id

    async def action_open(self, session_id: int, *, opening_cash: Decimal) -> str:
        """Abre el turno con su fondo declarado. Devuelve el número asignado."""
        session = await self._session(session_id)
        self._expect(session, ("draft",), "abrir")
        if opening_cash < ZERO:
            raise PosError(
                "POS_OPENING_CASH_INVALID",
                "El fondo de caja no puede ser negativo",
                hint="Declara el efectivo con el que parte el cajón, o cero.",
            )
        await self._refuse_second_open_session(session)

        sequences = SequenceService(self.env.session)
        await sequences.create(code=SEQUENCE_CODE, name="Turnos de caja", prefix="POS/")
        number = await sequences.next_by_code(SEQUENCE_CODE)

        await self.sessions.write(
            [session_id],
            {
                "state": "opened",
                "name": number,
                "opened_at": datetime.now(UTC),
                "opening_cash": money(opening_cash),
            },
        )
        return number

    async def _refuse_second_open_session(self, session: dict[str, Any]) -> None:
        """Una caja tiene un turno abierto o ninguno.

        Con dos turnos vivos sobre el mismo cajón el arqueo deja de significar
        nada: no se sabe contra qué fondo contar ni de quién es la diferencia.
        """
        others = await self.sessions.search(
            [
                ("config_id", "=", session["config_id"]),
                ("state", "in", ["opened", "closing"]),
            ],
            fields=["id", "name"],
            limit=1,
        )
        if others["rows"]:
            existing = others["rows"][0]
            raise PosError(
                "POS_SESSION_ALREADY_OPEN",
                f"La caja ya tiene el turno {existing['name'] or existing['id']} abierto",
                hint="Cierra el turno anterior con action_close antes de abrir otro.",
            )

    async def action_close_register(self, session_id: int) -> dict[str, Any]:
        """Cierra el turno a ventas nuevas, antes de contar el efectivo."""
        session = await self._session(session_id)
        self._expect(session, ("opened",), "cerrar a ventas")
        await self._refuse_pending_tickets(session)
        await self.sessions.write([session_id], {"state": "closing"})
        return {"session_id": session_id, "state": "closing"}

    async def action_close(
        self,
        session_id: int,
        *,
        counted_cash: Decimal,
        withdrawals: Decimal = ZERO,
        note: str = "",
    ) -> dict[str, Any]:
        """Arquea, asienta la diferencia si la hay, y deja el turno inmutable."""
        session = await self._session(session_id)
        self._expect(session, ("closing",), "arquear")
        if counted_cash < ZERO:
            raise PosError(
                "POS_COUNTED_CASH_REQUIRED",
                "El efectivo contado no puede ser negativo",
                hint="Un cierre sin efectivo contado no es un arqueo; declara cuánto había.",
            )
        config = await self.config(session["config_id"])
        received, change = await self._cash_movements(session)
        expected = expected_cash(
            opening=Decimal(str(session["opening_cash"] or ZERO)),
            cash_received=received,
            change_given=change,
            withdrawals=money(withdrawals),
        )
        gap = difference(money(counted_cash), expected)

        move_id = None
        if gap != ZERO:
            move_id = await self._book_difference(session, config, gap, note)

        await self.sessions.write(
            [session_id],
            {
                "state": "closed",
                "closed_at": datetime.now(UTC),
                "counted_cash": money(counted_cash),
                "expected_cash": expected,
                "difference": gap,
                "withdrawals": money(withdrawals),
                "move_id": move_id,
                "note": note or None,
            },
        )
        return {
            "session_id": session_id,
            "state": "closed",
            "expected_cash": str(expected),
            "counted_cash": str(money(counted_cash)),
            "difference": str(gap),
            "move_id": move_id,
        }

    async def _book_difference(
        self,
        session: dict[str, Any],
        config: dict[str, Any],
        gap: Decimal,
        note: str,
    ) -> int:
        """El faltante es una pérdida y el sobrante un ingreso, ambos contra caja.

        Se asienta y se contabiliza en el mismo acto: un faltante en borrador es
        un faltante que nadie mira.
        """
        amount = abs(gap)
        if gap < ZERO:
            lines = [
                {"account_id": config["difference_account_id"], "debit": amount, "credit": ZERO},
                {"account_id": config["cash_account_id"], "debit": ZERO, "credit": amount},
            ]
        else:
            lines = [
                {"account_id": config["cash_account_id"], "debit": amount, "credit": ZERO},
                {"account_id": config["difference_account_id"], "debit": ZERO, "credit": amount},
            ]
        label = "Faltante" if gap < ZERO else "Sobrante"
        move_id = await self.accounting.create_move(
            journal_id=config["cash_journal_id"],
            move_date=datetime.now(UTC).date(),
            currency_id=config["currency_id"],
            company_id=session["company_id"],
            lines=lines,
            ref=f"{label} de caja {session['name']}",
            narration=note or None,
        )
        await self.accounting.action_post(move_id)
        return move_id

    # ------------------------------------------------------------- costuras
    #
    # Los tickets llegan en F12-02b. Hasta entonces un turno solo mueve su
    # fondo y sus retiros, y estas dos costuras devuelven vacío en vez de
    # fingir datos: el arqueo de un turno sin ventas es exactamente el fondo.

    async def _cash_movements(self, session: dict[str, Any]) -> tuple[list[Decimal], list[Decimal]]:
        """Cobros en efectivo y vueltos entregados durante el turno."""
        return [], []

    async def _refuse_pending_tickets(self, session: dict[str, Any]) -> None:
        """Un turno con tickets sin cobrar no se puede cerrar."""
        return None
