"""Multi-operation transactions (design F2-04).

`atomic=True` is all-or-nothing; `atomic=False` isolates each operation in a
savepoint and reports per index, so a partial batch still tells the agent
exactly which items failed and why.
"""

from __future__ import annotations

from typing import Any

from ordo_core.environment import Environment
from ordo_core.errors import KernelError
from ordo_core.recordset import RecordSet

SUPPORTED_OPS = frozenset({"create", "write", "unlink"})


class TransactionRunner:
    def __init__(self, env: Environment) -> None:
        self.env = env

    async def run(
        self, operations: list[dict[str, Any]], *, atomic: bool = True, dry_run: bool = False
    ) -> list[dict[str, Any]]:
        outer = await self.env.session.begin_nested()
        results: list[dict[str, Any]] = []
        try:
            for index, operation in enumerate(operations):
                if atomic:
                    results.append(await self._execute(index, operation, dry_run=dry_run))
                else:
                    results.append(await self._execute_isolated(index, operation, dry_run))
        except Exception:
            await outer.rollback()
            raise
        if dry_run:
            await outer.rollback()
        else:
            await outer.commit()
        return results

    async def _execute_isolated(
        self, index: int, operation: dict[str, Any], dry_run: bool
    ) -> dict[str, Any]:
        savepoint = await self.env.session.begin_nested()
        try:
            result = await self._execute(index, operation, dry_run=dry_run)
        except KernelError as exc:
            await savepoint.rollback()
            return {
                "index": index,
                "ok": False,
                "error": {"code": exc.code, "message": exc.message, "hint": exc.hint},
            }
        await savepoint.commit()
        return result

    async def _execute(
        self, index: int, operation: dict[str, Any], *, dry_run: bool
    ) -> dict[str, Any]:
        op = operation.get("op")
        if op not in SUPPORTED_OPS:
            raise KernelError(
                "TX_UNKNOWN_OPERATION",
                f"Operación no soportada: {op!r}",
                hint=f"Operaciones válidas: {sorted(SUPPORTED_OPS)}",
            )
        model = operation.get("model")
        if not isinstance(model, str):
            raise KernelError("TX_UNKNOWN_OPERATION", "Falta el modelo de la operación")
        records = RecordSet(self.env, model)

        if op == "create":
            values = operation.get("values")
            values_list = values if isinstance(values, list) else [values or {}]
            outcome = await records.create(values_list, dry_run=dry_run)
            result = outcome if dry_run else {"ids": outcome}
        elif op == "write":
            outcome = await records.write(
                operation.get("ids", []),
                operation.get("values", {}),
                expected_version=operation.get("expected_version"),
                dry_run=dry_run,
            )
            result = {"written": outcome}
        else:
            outcome = await records.unlink(operation.get("ids", []), dry_run=dry_run)
            result = {"deleted": outcome}

        return {"index": index, "ok": True, "result": result}
