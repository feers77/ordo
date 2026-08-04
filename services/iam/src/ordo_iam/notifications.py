"""Envío de avisos a canales externos, siempre por la cola de jobs (F1-07).

El request que crea una aprobación no habla con la red: encola. Así una caída
de Telegram no convierte en 5xx una operación que ya está guardada, y el
reintento con backoff lo hace el worker.

`NotificationSender` es la única puerta de salida: en tests se inyecta la
implementación en memoria y no hay llamadas de red.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

from sqlalchemy.ext.asyncio import AsyncSession

from ordo_iam.jobs import JobQueue

NOTIFY_APPROVAL_JOB = "iam.notify_approval"
SEND_MESSAGE_JOB = "iam.send_message"


@dataclass(frozen=True)
class Button:
    """Botón de acción del mensaje; `callback_data` va firmado por el servidor."""

    label: str
    callback_data: str


@dataclass(frozen=True)
class OutboundMessage:
    address: str
    text: str
    buttons: tuple[Button, ...] = ()


@runtime_checkable
class NotificationSender(Protocol):
    async def send(self, message: OutboundMessage) -> None: ...


@dataclass
class InMemorySender:
    """Implementación para tests: registra en vez de salir a la red."""

    sent: list[OutboundMessage] = field(default_factory=list)

    async def send(self, message: OutboundMessage) -> None:
        self.sent.append(message)


class UnknownJobError(RuntimeError):
    pass


# -- encolado ----------------------------------------------------------------


async def enqueue_approval_notification(
    session: AsyncSession, *, approval_id: uuid.UUID, tenant: str
) -> int:
    return await JobQueue(session).enqueue(
        NOTIFY_APPROVAL_JOB,
        {"approval_id": str(approval_id), "tenant": tenant},
        priority=10,  # una aprobación bloquea a un agente: va antes que el resto
    )


async def enqueue_message(session: AsyncSession, *, address: str, body: str) -> int:
    return await JobQueue(session).enqueue(SEND_MESSAGE_JOB, {"address": address, "text": body})


# -- worker ------------------------------------------------------------------


async def _dispatch(
    session: AsyncSession, sender: NotificationSender, name: str, payload: dict[str, Any]
) -> None:
    if name == NOTIFY_APPROVAL_JOB:
        # Import diferido: telegram.py renderiza el mensaje y necesita este módulo.
        from ordo_iam.telegram import build_approval_messages

        for message in await build_approval_messages(session, uuid.UUID(payload["approval_id"])):
            await sender.send(message)
        return
    if name == SEND_MESSAGE_JOB:
        await sender.send(OutboundMessage(address=payload["address"], text=payload["text"]))
        return
    raise UnknownJobError(name)


async def run_pending_notifications(
    session: AsyncSession,
    sender: NotificationSender,
    *,
    worker: str = "iam-notifier",
    limit: int = 10,
) -> int:
    """Procesa un lote de jobs pendientes; devuelve cuántos terminaron bien."""
    queue = JobQueue(session)
    jobs = await queue.claim(worker, limit)
    await session.commit()  # el claim se hace visible antes de cualquier envío
    processed = 0
    for job in jobs:
        try:
            await _dispatch(session, sender, job["name"], job["payload"])
        except Exception as exc:  # el error va al job, no tumba el lote
            await session.rollback()  # el claim ya está commiteado; esto sólo limpia el fallo
            await queue.fail(job["id"], f"{type(exc).__name__}: {exc}")
        else:
            await queue.complete(job["id"])
            processed += 1
        await session.commit()
    return processed
