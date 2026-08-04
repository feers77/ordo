"""Presupuesto de queries por operación (PLAN §10: detección de N+1).

Un ORM genérico degrada en silencio: una operación que hoy hace 2 queries
mañana hace 2+N sin que nadie lo note. Estos tests cuentan las queries y
fallan si el número crece, así que la regresión aparece en el PR que la
introduce y no en producción.
"""

from __future__ import annotations

import pytest
from ordo_core.recordset import RecordSet
from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncSession

from tests.integration.core.helpers import make_partner_env

pytestmark = pytest.mark.integration


# El Environment re-aplica el binding de tenant al abrir cada transacción.
# Son sentencias de infraestructura, no consultas de negocio: se excluyen del
# presupuesto para que este mida lo que de verdad puede degradarse.
BINDING_MARKERS = ("set_config", "SET LOCAL ROLE")


class QueryCounter:
    """Cuenta las consultas de negocio enviadas al motor."""

    def __init__(self, session: AsyncSession) -> None:
        self.engine = session.sync_session.get_bind()
        self.statements: list[str] = []

    def __enter__(self) -> QueryCounter:
        event.listen(self.engine, "before_cursor_execute", self._record)
        return self

    def __exit__(self, *exc: object) -> None:
        event.remove(self.engine, "before_cursor_execute", self._record)

    def _record(self, conn, cursor, statement, parameters, context, executemany) -> None:  # type: ignore[no-untyped-def]
        if any(marker in statement for marker in BINDING_MARKERS):
            return
        self.statements.append(statement)

    @property
    def count(self) -> int:
        return len(self.statements)


class TestQueryBudget:
    async def test_read_batch_is_one_query(self, core_session: AsyncSession) -> None:
        """Leer N registros cuesta una query, no N."""
        env = await make_partner_env(core_session, "qb1")
        records = RecordSet(env, "res.partner")
        ids = await records.create([{"name": f"P{i}"} for i in range(20)])
        await core_session.commit()

        with QueryCounter(core_session) as counter:
            rows = await records.read(ids, fields=["name"])
        assert len(rows) == 20
        assert counter.count == 1, f"esperaba 1 query, hubo {counter.count}"

    async def test_create_batch_is_one_insert(self, core_session: AsyncSession) -> None:
        env = await make_partner_env(core_session, "qb2")
        records = RecordSet(env, "res.partner")
        with QueryCounter(core_session) as counter:
            await records.create([{"name": f"C{i}"} for i in range(10)])
        inserts = [s for s in counter.statements if s.lstrip().upper().startswith("INSERT")]
        assert len(inserts) == 1, f"esperaba 1 INSERT para 10 registros, hubo {len(inserts)}"

    async def test_write_batch_is_one_update(self, core_session: AsyncSession) -> None:
        env = await make_partner_env(core_session, "qb3")
        records = RecordSet(env, "res.partner")
        ids = await records.create([{"name": f"W{i}"} for i in range(10)])
        await core_session.commit()

        with QueryCounter(core_session) as counter:
            await records.write(ids, {"ref": "masivo"})
        updates = [s for s in counter.statements if s.lstrip().upper().startswith("UPDATE")]
        assert len(updates) == 1

    async def test_search_with_join_is_still_one_query(self, core_session: AsyncSession) -> None:
        """Una ruta punteada genera JOIN, no una consulta por registro."""
        env = await make_partner_env(core_session, "qb4")
        records = RecordSet(env, "res.partner")
        await records.create([{"name": f"S{i}"} for i in range(15)])
        await core_session.commit()

        with QueryCounter(core_session) as counter:
            result = await records.search([("name", "like", "S")], fields=["name"], limit=50)
        assert len(result["rows"]) == 15
        selects = [s for s in counter.statements if s.lstrip().upper().startswith("SELECT")]
        assert len(selects) == 1

    async def test_optimistic_check_costs_one_extra_query(self, core_session: AsyncSession) -> None:
        """El bloqueo optimista cuesta exactamente una lectura de versiones."""
        env = await make_partner_env(core_session, "qb5")
        records = RecordSet(env, "res.partner")
        ids = await records.create([{"name": f"V{i}"} for i in range(5)])
        await core_session.commit()

        with QueryCounter(core_session) as counter:
            await records.write(ids, {"ref": "x"}, expected_version=1)
        assert counter.count == 2  # SELECT de versiones + UPDATE
