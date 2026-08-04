"""Webhook delivery worker: the outbox reaches whoever subscribed.

An agent that polls is an agent that arrives late. This loop walks every
tenant schema every few seconds, dispatches what each subscription has not
seen yet and retries what failed. Fail-soft per tenant: a broken tenant
logs and the rest of the round still runs.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING

import httpx
from modules.webhook.service import WebhookService
from ordo_core.sandbox import ensure_registry_table, purge_expired
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from ordo_events import deps

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from fastapi import FastAPI
    from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

logger = logging.getLogger("ordo.events.worker")

DEFAULT_INTERVAL_S = 5.0
TENANT_PREFIX = "t_"
REQUEST_TIMEOUT_S = 10.0
# Sin respuesta del otro lado no hay código HTTP que reportar: se sintetiza.
NO_RESPONSE_STATUS = 599

TENANT_QUERY = (
    "SELECT schema_name FROM information_schema.schemata WHERE schema_name LIKE 't\\_%' ESCAPE '\\'"
)

_admin_engine: AsyncEngine | None = None


class HttpxTransport:
    """Real HTTP delivery. One client per worker, so connections are reused."""

    def __init__(self, timeout: float = REQUEST_TIMEOUT_S) -> None:
        self._client = httpx.AsyncClient(timeout=timeout)

    async def send(self, url: str, body: bytes, headers: dict[str, str]) -> int:
        try:
            response = await self._client.post(url, content=body, headers=headers)
        except httpx.HTTPError as exc:
            logger.warning("entrega a %s falló en transporte: %s", url, exc)
            return NO_RESPONSE_STATUS
        return response.status_code

    async def aclose(self) -> None:
        await self._client.aclose()


async def discover_tenants(engine: AsyncEngine) -> list[str]:
    """Tenant names behind the `t_*` schemas, with the prefix stripped."""
    async with engine.connect() as connection:
        rows = (await connection.execute(text(TENANT_QUERY))).all()
    return sorted(str(row.schema_name).removeprefix(TENANT_PREFIX) for row in rows)


def admin_engine() -> AsyncEngine | None:
    """Owner-role engine for sandbox DDL, or None if the deploy has no DDL.

    Dropping a schema is DDL and `ORDO_DATABASE_URL` deliberately points at a
    role without it (AGENTS.md §7): without the admin URL the worker simply
    does not collect sandboxes.
    """
    global _admin_engine
    url = os.environ.get("ORDO_ADMIN_DATABASE_URL")
    if not url:
        return None
    if _admin_engine is None:
        _admin_engine = create_async_engine(url, pool_size=1, max_overflow=1)
    return _admin_engine


async def purge_sandboxes() -> None:
    """Drops the sandboxes whose TTL ran out (F3-03 §3)."""
    engine = admin_engine()
    if engine is None:
        return
    maker = async_sessionmaker(engine, expire_on_commit=False)
    async with maker() as session:
        await ensure_registry_table(session)
        dropped = await purge_expired(session)
    if dropped:
        logger.info("sandboxes vencidos borrados: %d (%s)", len(dropped), ", ".join(dropped))


async def worker_loop(interval_s: float | None = None) -> None:
    interval = interval_s or float(os.environ.get("ORDO_EVENTS_INTERVAL", DEFAULT_INTERVAL_S))
    transport = HttpxTransport()
    maker = deps.session_maker()
    logger.info("worker de webhooks activo (cada %.1fs)", interval)
    while True:
        try:
            tenants = await discover_tenants(deps.engine())
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("no se pudieron descubrir los tenants; se reintenta")
            tenants = []
        for tenant in tenants:
            try:
                await _run_tenant(maker, tenant, transport)
            except asyncio.CancelledError:
                raise
            except Exception:
                # Un tenant roto no frena a los demás: se registra y se sigue.
                logger.exception("ronda de webhooks del tenant %s falló", tenant)
        try:
            await purge_sandboxes()
        except asyncio.CancelledError:
            raise
        except Exception:
            # La basura de sandboxes es secundaria: nunca frena las entregas.
            logger.exception("la limpieza de sandboxes vencidos falló")
        await asyncio.sleep(interval)


async def _run_tenant(
    maker: async_sessionmaker[AsyncSession],
    tenant: str,
    transport: HttpxTransport,
) -> None:
    async with maker() as session:
        env = await deps.build_env(session, tenant)
        service = WebhookService(env)
        dispatched = await service.dispatch_pending(transport)
        retried = await service.retry_failed(transport)
        await session.commit()
    if dispatched["delivered"] or dispatched["failed"] or retried["delivered"]:
        logger.info(
            "webhooks %s: entregados %d, fallidos %d, reintentos ok %d",
            tenant,
            dispatched["delivered"],
            dispatched["failed"],
            retried["delivered"],
        )


def worker_enabled() -> bool:
    """Active only when there is a database to read the outbox from."""
    return bool(os.environ.get("ORDO_DATABASE_URL"))


def install_worker(app: FastAPI) -> None:
    """Wraps the app lifespan to start and stop the worker."""
    original = app.router.lifespan_context

    @asynccontextmanager
    async def with_worker(app_: FastAPI) -> AsyncIterator[None]:
        async with original(app_):
            task: asyncio.Task[None] | None = None
            if worker_enabled():
                task = asyncio.create_task(worker_loop(), name="events-webhook-worker")
            else:
                logger.info("worker de webhooks inactivo (sin ORDO_DATABASE_URL)")
            try:
                yield
            finally:
                if task is not None:
                    task.cancel()
                    with contextlib.suppress(asyncio.CancelledError):
                        await task

    app.router.lifespan_context = with_worker
