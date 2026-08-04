"""Worker de notificaciones: drena la cola `ir_job` dentro del proceso IAM.

Hasta ahora `run_pending_notifications` existía y estaba probado, pero nadie
lo ejecutaba en producción: los avisos de aprobación se encolaban y morían en
la tabla. Este loop corre como tarea de fondo del propio servicio; con más de
una réplica no hay duplicados porque el claim usa FOR UPDATE SKIP LOCKED.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING

from sqlalchemy.ext.asyncio import async_sessionmaker

from ordo_iam import db
from ordo_iam.notifications import run_pending_notifications
from ordo_iam.telegram import sender_from_env

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from fastapi import FastAPI

logger = logging.getLogger("ordo.iam.worker")

DEFAULT_INTERVAL_S = 5.0


def worker_enabled() -> bool:
    """Activo si hay canal configurado y no se apagó explícitamente."""
    if os.environ.get("ORDO_NOTIFY_WORKER", "1") == "0":
        return False
    return bool(os.environ.get("TELEGRAM_BOT_TOKEN"))


async def worker_loop(interval_s: float | None = None) -> None:
    interval = interval_s or float(os.environ.get("ORDO_NOTIFY_INTERVAL", DEFAULT_INTERVAL_S))
    sender = sender_from_env()
    maker = async_sessionmaker(db.engine(), expire_on_commit=False)
    logger.info("worker de notificaciones activo (cada %.1fs)", interval)
    while True:
        try:
            async with maker() as session:
                processed = await run_pending_notifications(session, sender)
                if processed:
                    logger.info("notificaciones enviadas: %d", processed)
        except asyncio.CancelledError:
            raise
        except Exception:
            # El worker sobrevive a cualquier error: el job fallido ya quedó
            # reprogramado con backoff por la cola; aquí solo se registra.
            logger.exception("lote de notificaciones falló; se reintenta")
        await asyncio.sleep(interval)


def install_worker(app: FastAPI) -> None:
    """Envuelve el lifespan de la app para arrancar y apagar el worker."""
    original = app.router.lifespan_context

    @asynccontextmanager
    async def with_worker(app_: FastAPI) -> AsyncIterator[None]:
        async with original(app_):
            task: asyncio.Task[None] | None = None
            if worker_enabled():
                task = asyncio.create_task(worker_loop(), name="iam-notify-worker")
            else:
                logger.info("worker de notificaciones inactivo (sin TELEGRAM_BOT_TOKEN)")
            try:
                yield
            finally:
                if task is not None:
                    task.cancel()
                    with contextlib.suppress(asyncio.CancelledError):
                        await task

    app.router.lifespan_context = with_worker
