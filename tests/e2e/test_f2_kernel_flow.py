"""E2E del kernel (F2): ciclo completo de un documento de negocio.

Recorre lo que un agente haría de punta a punta contra Postgres real:
descubrir el schema, crear con secuencia legal, simular antes de escribir,
reintentar con idempotencia, chocar con bloqueo optimista, dejar rastro en
el chatter, adjuntar un documento y emitir el evento al outbox.
"""

import uuid
from collections.abc import AsyncIterator
from decimal import Decimal

import pytest
from ordo_core import Environment
from ordo_core.errors import KernelError
from ordo_core.fields import Char, Monetary, Selection
from ordo_core.model import Model
from ordo_core.recordset import RecordSet
from ordo_core.registry import Module, Registry
from ordo_core.semantic import build_schema
from ordo_core.services import (
    AttachmentService,
    Chatter,
    InMemoryStorage,
    JobQueue,
    Outbox,
    SequenceService,
)
from ordo_core.services.schema import create_kernel_tables
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

pytestmark = pytest.mark.e2e


def sales_registry() -> Registry:
    class SaleOrder(Model):
        _name = "sale.order"
        _description = "Orden de venta"

        name = Char(required=True, agent_hint="Número del documento", examples=["SO00001"])
        partner_name = Char(required=True, agent_hint="Nombre del cliente", examples=["ACME"])
        amount_total = Monetary(agent_hint="Total del documento", examples=["11900.00"])
        state = Selection(
            [("draft", "Borrador"), ("sale", "Confirmada"), ("cancel", "Anulada")],
            default="draft",
            agent_hint="Estado del ciclo de vida",
            examples=["draft"],
        )

    return Registry.build([Module("sales", models=[SaleOrder])])


@pytest.fixture
async def env(e2e_db_url: str) -> AsyncIterator[Environment]:
    """Tenant propio por test: la secuencia legal no arrastra estado."""
    tenant = f"k{uuid.uuid4().hex[:8]}"
    engine = create_async_engine(e2e_db_url)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    session: AsyncSession = maker()
    schema = f"t_{tenant}"
    await session.execute(text(f'CREATE SCHEMA IF NOT EXISTS "{schema}"'))
    await session.execute(
        text(
            f'CREATE TABLE IF NOT EXISTS "{schema}".sale_order ('
            "id serial PRIMARY KEY, name text NOT NULL, partner_name text NOT NULL, "
            "amount_total numeric(18,2), state text DEFAULT 'draft', "
            "create_uid integer, create_date timestamptz DEFAULT now(), "
            "write_uid integer, write_date timestamptz DEFAULT now(), "
            "version integer DEFAULT 1)"
        )
    )
    await session.commit()
    environment = Environment(
        session=session, tenant=tenant, registry=sales_registry(), app_role=None
    )
    await environment.bind()
    await create_kernel_tables(session)
    await session.commit()
    yield environment
    await session.close()
    await engine.dispose()


class TestKernelEndToEnd:
    async def test_full_document_lifecycle(self, env: Environment) -> None:
        session = env.session

        # 1. El agente descubre el modelo por el schema semántico
        schema = build_schema(env.registry, models=["sale.order"])
        sale_schema = schema["models"][0]
        assert sale_schema["fields"]["state"]["values"] == ["draft", "sale", "cancel"]
        assert sale_schema["fields"]["amount_total"]["hint"]
        assert "decimales" in schema["conventions"]["money"]

        # 2. Secuencia legal sin huecos para numerar el documento
        sequences = SequenceService(session)
        await sequences.create(
            code="sale.order", name="Ventas", prefix="SO", padding=5, implementation="no_gap"
        )
        number = await sequences.next_by_code("sale.order")
        assert number == "SO00001"

        orders = RecordSet(env, "sale.order")

        # 3. Simula antes de escribir: un campo faltante se reporta sin persistir
        simulation = await orders.create(
            [{"name": number, "amount_total": Decimal("11900.00")}], dry_run=True
        )
        assert simulation["validations"][0]["code"] == "FIELD_REQUIRED"
        assert (await orders.search([]))["rows"] == []

        # 4. Creación real, ya con el cliente
        [order_id] = await orders.create(
            [
                {
                    "name": number,
                    "partner_name": "ACME SpA",
                    "amount_total": Decimal("11900.00"),
                }
            ]
        )
        await session.commit()

        # 5. El agente deja constancia y sigue el documento
        chatter = Chatter(session)
        await chatter.post(
            model="sale.order",
            res_id=order_id,
            body="Documento creado automáticamente desde la cotización.",
            author_kind="agent",
            author_principal="agent:e2e",
        )
        await chatter.follow("sale.order", order_id, "user:1")

        # 6. Confirmar el documento deja rastro de qué cambió
        await orders.write([order_id], {"state": "sale"}, expected_version=1)
        await chatter.track_changes(
            model="sale.order",
            res_id=order_id,
            changes={"state": ("draft", "sale")},
            author_kind="agent",
            author_principal="agent:e2e",
        )
        thread = await chatter.thread("sale.order", order_id)
        assert [m["author_kind"] for m in thread] == ["agent", "agent"]
        assert "sale" in thread[1]["body"]

        # 7. Otro actor con una versión vieja no puede pisar el cambio
        with pytest.raises(KernelError) as conflict:
            await orders.write([order_id], {"state": "cancel"}, expected_version=1)
        assert conflict.value.code == "CONCURRENT_MODIFICATION"
        assert conflict.value.current_state is not None
        assert conflict.value.current_state[0]["state"] == "sale"

        # 8. Adjuntar el PDF del documento, con deduplicación por contenido
        storage = InMemoryStorage()
        attachments = AttachmentService(session, storage)
        pdf = b"%PDF-1.7 contenido del documento"
        first = await attachments.upload(
            name="SO00001.pdf",
            data=pdf,
            mimetype="application/pdf",
            model="sale.order",
            res_id=order_id,
        )
        duplicate = await attachments.upload(
            name="copia.pdf",
            data=pdf,
            mimetype="application/pdf",
            model="sale.order",
            res_id=order_id,
        )
        assert duplicate["deduplicated"] is True
        assert len(storage.objects) == 1
        assert await attachments.download(first["id"]) == pdf

        # 9. El evento sale por el outbox y el trabajo pesado queda encolado
        outbox = Outbox(session)
        await outbox.emit(
            "sale.order.confirmed", "ordo.sale.order", {"id": order_id, "number": number}
        )
        jobs = JobQueue(session)
        await jobs.enqueue("render_pdf", {"order_id": order_id})
        await session.commit()

        published: list[str] = []

        async def publish(subject: str, msg_id: str, payload: dict[str, object]) -> None:
            published.append(subject)

        assert await outbox.relay(publish) == 1
        assert published == ["ordo.sale.order"]
        assert await outbox.relay(publish) == 1 - 1  # no republica lo ya enviado

        claimed = await jobs.claim("worker-e2e")
        assert claimed[0]["name"] == "render_pdf"
        await jobs.complete(claimed[0]["id"])

        # 10. Estado final coherente
        [final] = await orders.read([order_id], fields=["name", "state", "amount_total"])
        assert final["state"] == "sale"
        assert str(final["amount_total"]) == "11900.00"
        await session.commit()

    async def test_second_document_continues_the_sequence(self, env: Environment) -> None:
        """La secuencia legal no reinicia ni salta entre documentos."""
        sequences = SequenceService(env.session)
        await sequences.create(
            code="sale.order", name="Ventas", prefix="SO", padding=5, implementation="no_gap"
        )
        numbers = [await sequences.next_by_code("sale.order") for _ in range(3)]
        assert numbers == ["SO00001", "SO00002", "SO00003"]
        await env.session.commit()
