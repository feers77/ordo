"""Conciliación de partidas: cerrar el círculo entre factura y pago.

Conciliar es afirmar que un conjunto de partidas de la misma cuenta se salda
entre sí. La suma debe dar exactamente cero: la conciliación parcial vendrá
después como concepto propio, no como un grupo que "casi" cuadra.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from ordo_core.environment import Environment
from ordo_core.recordset import RecordSet

from modules.account.services import AccountingError

ZERO = Decimal("0")


class ReconcileError(AccountingError):
    """Error de conciliación con código estable."""


class ReconcileService:
    def __init__(self, env: Environment) -> None:
        self.env = env
        self.lines = RecordSet(env, "account.move.line")
        self.groups = RecordSet(env, "account.reconcile")

    async def reconcile(self, line_ids: list[int]) -> int:
        """Concilia las partidas; devuelve el id del grupo creado."""
        if len(set(line_ids)) < 2:
            raise ReconcileError(
                "RECONCILE_TOO_FEW",
                "Conciliar requiere al menos dos partidas distintas",
            )
        rows = await self.lines.read(
            list(set(line_ids)),
            fields=["id", "account_id", "debit", "credit", "reconciled", "move_id", "company_id"],
        )
        if len(rows) != len(set(line_ids)):
            raise ReconcileError("RECONCILE_LINE_NOT_FOUND", "Alguna partida no existe")

        accounts = {row["account_id"] for row in rows}
        if len(accounts) != 1:
            raise ReconcileError(
                "RECONCILE_MIXED_ACCOUNTS",
                "Todas las partidas de un grupo comparten la misma cuenta",
            )
        account_id = accounts.pop()
        [account] = await RecordSet(self.env, "account.account").read(
            [account_id], fields=["reconcile", "code", "name"]
        )
        if not account["reconcile"]:
            raise ReconcileError(
                "RECONCILE_ACCOUNT_NOT_RECONCILABLE",
                f"La cuenta {account['code']} {account['name']} no es conciliable",
                hint="Marca reconcile=true en la cuenta si corresponde.",
            )
        if any(row["reconciled"] for row in rows):
            raise ReconcileError(
                "RECONCILE_ALREADY_RECONCILED",
                "Alguna partida ya pertenece a otro grupo de conciliación",
            )

        moves = await RecordSet(self.env, "account.move").read(
            list({row["move_id"] for row in rows}), fields=["id", "state"]
        )
        if any(move["state"] != "posted" for move in moves):
            raise ReconcileError(
                "RECONCILE_UNPOSTED_MOVE",
                "Solo se concilian partidas de asientos contabilizados",
            )

        balance = sum((row["debit"] - row["credit"] for row in rows), ZERO)
        if balance != ZERO:
            raise ReconcileError(
                "RECONCILE_UNBALANCED",
                f"El grupo no salda: la diferencia es {balance}",
                hint="La suma del debe y del haber de las partidas debe ser idéntica.",
            )

        [group_id] = await self.groups.create(
            [{"account_id": account_id, "company_id": rows[0]["company_id"]}]
        )
        await self.lines.write(
            [row["id"] for row in rows], {"reconciled": True, "reconcile_id": group_id}
        )
        return group_id

    async def unreconcile(self, group_id: int) -> int:
        """Deshace el grupo; devuelve cuántas partidas liberó."""
        result = await self.lines.search(
            [("reconcile_id", "=", group_id)], fields=["id"], limit=1000, active_test=False
        )
        ids = [row["id"] for row in result["rows"]]
        if not ids:
            raise ReconcileError(
                "RECONCILE_GROUP_NOT_FOUND", f"No existe el grupo {group_id} o está vacío"
            )
        await self.lines.write(ids, {"reconciled": False, "reconcile_id": None})
        await self.groups.unlink([group_id])
        return len(ids)


async def open_items(
    env: Environment, *, account_id: int, partner_id: int | None = None
) -> list[dict[str, Any]]:
    """Partidas abiertas (sin conciliar) de una cuenta, para que un agente elija."""
    domain: list[Any] = [
        ("account_id", "=", account_id),
        ("reconciled", "=", False),
        ("move_id.state", "=", "posted"),
    ]
    if partner_id is not None:
        domain.append(("partner_id", "=", partner_id))
    result = await RecordSet(env, "account.move.line").search(
        domain,
        fields=["id", "move_id", "name", "debit", "credit", "partner_id", "date_maturity"],
        limit=500,
    )
    return list(result["rows"])
