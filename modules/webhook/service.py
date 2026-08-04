"""Servicio de webhooks: el outbox llega firmado a quien se suscribió.

El watermark vive en la bitácora de entregas, no en memoria: cada evento
del outbox produce exactamente una fila por suscripción —entregada, fallida
u omitida por patrón— así que un worker que se cae a mitad de lote reanuda
donde iba y nadie recibe el mismo evento dos veces por accidente.

La firma es HMAC-SHA256 sobre el cuerpo exacto que viaja: el receptor
verifica origen e integridad sin PKI, comparando la cabecera
X-Ordo-Signature contra su copia del secreto.
"""

from __future__ import annotations

import fnmatch
import hashlib
import hmac
import json
import secrets
from datetime import UTC, datetime
from typing import Any, Protocol

from ordo_core.environment import Environment
from ordo_core.errors import KernelError
from ordo_core.recordset import RecordSet
from ordo_core.services.outbox import Outbox

#: Fallos consecutivos que suspenden una suscripción.
MAX_CONSECUTIVE_FAILURES = 10
#: Código sintético cuando el transporte ni siquiera obtuvo respuesta.
NO_RESPONSE_STATUS = 599
#: Cuántas suscripciones y entregas se recorren por pasada.
SUBSCRIPTION_PAGE = 200
DELIVERY_PAGE = 1000

SUBSCRIPTION_FIELDS = [
    "id",
    "name",
    "url",
    "event_pattern",
    "secret",
    "state",
    "failure_count",
    "company_id",
]

DELIVERY_FIELDS = [
    "id",
    "subscription_id",
    "event_id",
    "event_type",
    "status",
    "attempts",
    "company_id",
]


class WebhookError(KernelError):
    """Error de webhooks con código estable."""


def generate_secret() -> str:
    """Secreto de firma de una suscripción: 32 bytes en hexadecimal."""
    return secrets.token_hex(32)


def sign(body: bytes, secret: str) -> str:
    """Firma HMAC-SHA256 del cuerpo, en el formato de la cabecera."""
    digest = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return f"sha256={digest}"


def pattern_matches(event_type: str, pattern: str) -> bool:
    """Verdadero si el tipo de evento calza con el patrón fnmatch."""
    return fnmatch.fnmatch(event_type, pattern)


class DeliveryTransport(Protocol):
    """Quien pone el evento en la red. HTTP real en el worker, stub en tests."""

    async def send(self, url: str, body: bytes, headers: dict[str, str]) -> int: ...


class WebhookService:
    def __init__(self, env: Environment) -> None:
        self.env = env
        self.subscriptions = RecordSet(env, "webhook.subscription")
        self.deliveries = RecordSet(env, "webhook.delivery")
        self.outbox = Outbox(env.session)

    # ------------------------------------------------------------- creación

    async def create_subscription(
        self,
        *,
        name: str,
        url: str,
        event_pattern: str,
        company_id: int | None = None,
    ) -> dict[str, Any]:
        """Crea la suscripción y devuelve su secreto, la única vez que se ve.

        La creación pasa por aquí y no por la API genérica porque el secreto
        lo genera el servidor: un secreto elegido por el cliente no prueba
        nada sobre el origen del evento.
        """
        if not url.startswith(("http://", "https://")):
            raise WebhookError(
                "WEBHOOK_URL_INVALID",
                f"La URL de destino debe ser http o https: {url!r}",
                hint="Usa una URL absoluta, por ejemplo https://ejemplo.com/hooks/ordo.",
            )
        if not event_pattern.strip():
            raise WebhookError(
                "WEBHOOK_INVALID_PATTERN",
                "El patrón de eventos no puede ir vacío",
                hint="Usa '*' para recibir todo o 'sale.order.*' para una familia.",
            )
        secret = generate_secret()
        [subscription_id] = await self.subscriptions.create(
            [
                {
                    "name": name,
                    "url": url,
                    "event_pattern": event_pattern,
                    "secret": secret,
                    "state": "active",
                    "failure_count": 0,
                    "company_id": company_id,
                }
            ]
        )
        return {"id": subscription_id, "secret": secret}

    # -------------------------------------------------------------- entrega

    async def dispatch_pending(
        self, transport: DeliveryTransport, *, limit: int = 100
    ) -> dict[str, int]:
        """Entrega a cada suscripción activa lo que aún no ha visto."""
        totals = {"delivered": 0, "failed": 0}
        page = await self.subscriptions.search(
            [("state", "=", "active")],
            fields=SUBSCRIPTION_FIELDS,
            limit=SUBSCRIPTION_PAGE,
        )
        for subscription in page["rows"]:
            await self._dispatch_one(subscription, transport, limit, totals)
        return totals

    async def _dispatch_one(
        self,
        subscription: dict[str, Any],
        transport: DeliveryTransport,
        limit: int,
        totals: dict[str, int],
    ) -> None:
        watermark = await self._watermark(subscription["id"])
        events = await self.outbox.since(watermark, limit)
        failures = int(subscription["failure_count"] or 0)
        for event in events:
            if not pattern_matches(event["event_type"], subscription["event_pattern"]):
                await self._skip(subscription, event)
                continue
            delivery_id = await self._new_delivery(subscription, event)
            body = self._body(event)
            status, error = await self._send(
                transport,
                url=subscription["url"],
                secret=subscription["secret"],
                event_type=event["event_type"],
                delivery_id=delivery_id,
                body=body,
            )
            if _is_success(status):
                await self._mark_delivered(subscription["id"], delivery_id, status, attempts=1)
                failures = 0
                totals["delivered"] += 1
                continue
            await self._mark_failed(delivery_id, status, error, attempts=1)
            failures += 1
            totals["failed"] += 1
            if await self._register_failures(subscription["id"], failures):
                # Suspendida: el resto del lote se le entregará al reanudarla.
                return

    async def retry_failed(
        self,
        transport: DeliveryTransport,
        *,
        max_attempts: int = 5,
        limit: int = 100,
    ) -> dict[str, int]:
        """Reintenta las entregas fallidas con menos de `max_attempts` intentos.

        La re-entrega lleva el MISMO X-Ordo-Delivery que el primer intento,
        para que el receptor deduplique sin adivinar.
        """
        totals = {"delivered": 0, "failed": 0}
        page = await self.deliveries.search(
            [("status", "=", "failed"), ("attempts", "<", max_attempts)],
            fields=DELIVERY_FIELDS,
            limit=limit,
        )
        for delivery in page["rows"]:
            subscription = await self._subscription(delivery["subscription_id"])
            if subscription["state"] != "active":
                # Una suscripción suspendida no recibe: para eso se suspende.
                continue
            event = await self._event(delivery["event_id"])
            if event is None:
                continue
            attempts = int(delivery["attempts"] or 0) + 1
            status, error = await self._send(
                transport,
                url=subscription["url"],
                secret=subscription["secret"],
                event_type=event["event_type"],
                delivery_id=delivery["id"],
                body=self._body(event),
            )
            if _is_success(status):
                await self._mark_delivered(
                    subscription["id"], delivery["id"], status, attempts=attempts
                )
                totals["delivered"] += 1
                continue
            await self._mark_failed(delivery["id"], status, error, attempts=attempts)
            failures = int(subscription["failure_count"] or 0) + 1
            await self._register_failures(subscription["id"], failures)
            totals["failed"] += 1
        return totals

    # ------------------------------------------------------------ acciones

    async def action_suspend(self, subscription_id: int) -> None:
        """Deja de entregar sin borrar la suscripción ni su historia."""
        await self._subscription(subscription_id)
        await self.subscriptions.write([subscription_id], {"state": "suspended"})

    async def action_resume(self, subscription_id: int) -> None:
        """Reanuda las entregas y perdona los fallos acumulados."""
        await self._subscription(subscription_id)
        await self.subscriptions.write([subscription_id], {"state": "active", "failure_count": 0})

    # -------------------------------------------------------------- internos

    async def _send(
        self,
        transport: DeliveryTransport,
        *,
        url: str,
        secret: str,
        event_type: str,
        delivery_id: int,
        body: bytes,
    ) -> tuple[int, str | None]:
        headers = {
            "Content-Type": "application/json",
            "X-Ordo-Event": event_type,
            "X-Ordo-Delivery": str(delivery_id),
            "X-Ordo-Signature": sign(body, secret),
        }
        try:
            return await transport.send(url, body, headers), None
        except Exception as exc:
            # El transporte falla de mil formas (DNS, TLS, timeout): todas son
            # el mismo hecho de negocio, "no se pudo entregar".
            return NO_RESPONSE_STATUS, str(exc)

    def _body(self, event: dict[str, Any]) -> bytes:
        """Cuerpo canónico: lo que se firma es exactamente lo que viaja."""
        return json.dumps(
            {
                "event_id": event["id"],
                "event_type": event["event_type"],
                "subject": event["subject"],
                "payload": event["payload"],
                "tenant": self.env.tenant,
            },
            default=str,
            sort_keys=True,
        ).encode()

    async def _watermark(self, subscription_id: int) -> int:
        """Mayor evento ya visto por la suscripción; 0 si nunca vio ninguno."""
        watermark = 0
        cursor: str | None = None
        while True:
            page = await self.deliveries.search(
                [("subscription_id", "=", subscription_id)],
                fields=["id", "event_id"],
                limit=DELIVERY_PAGE,
                cursor=cursor,
            )
            for row in page["rows"]:
                watermark = max(watermark, int(row["event_id"]))
            cursor = page["next_cursor"]
            if cursor is None:
                return watermark

    async def _new_delivery(
        self, subscription: dict[str, Any], event: dict[str, Any], *, status: str = "pending"
    ) -> int:
        [delivery_id] = await self.deliveries.create(
            [
                {
                    "subscription_id": subscription["id"],
                    "event_id": int(event["id"]),
                    "event_type": event["event_type"],
                    "status": status,
                    "attempts": 0,
                    "company_id": subscription["company_id"],
                }
            ]
        )
        return int(delivery_id)

    async def _skip(self, subscription: dict[str, Any], event: dict[str, Any]) -> None:
        """Evento fuera del patrón: se anota como visto y el watermark avanza."""
        await self._new_delivery(subscription, event, status="skipped")

    async def _mark_delivered(
        self, subscription_id: int, delivery_id: int, status: int, *, attempts: int
    ) -> None:
        now = datetime.now(UTC)
        await self.deliveries.write(
            [delivery_id],
            {
                "status": "delivered",
                "attempts": attempts,
                "response_status": status,
                "delivered_at": now,
                "error": None,
            },
        )
        # Un éxito perdona los fallos anteriores: el contador es de racha.
        await self.subscriptions.write(
            [subscription_id], {"last_delivery_at": now, "failure_count": 0}
        )

    async def _mark_failed(
        self, delivery_id: int, status: int, error: str | None, *, attempts: int
    ) -> None:
        await self.deliveries.write(
            [delivery_id],
            {
                "status": "failed",
                "attempts": attempts,
                "response_status": status,
                "error": error or f"HTTP {status}",
            },
        )

    async def _register_failures(self, subscription_id: int, failures: int) -> bool:
        """Anota los fallos consecutivos. Devuelve True si quedó suspendida."""
        values: dict[str, Any] = {"failure_count": failures}
        suspended = failures >= MAX_CONSECUTIVE_FAILURES
        if suspended:
            values["state"] = "suspended"
        await self.subscriptions.write([subscription_id], values)
        return suspended

    async def _subscription(self, subscription_id: int) -> dict[str, Any]:
        rows = await self.subscriptions.read([subscription_id], fields=SUBSCRIPTION_FIELDS)
        if not rows:
            raise WebhookError(
                "WEBHOOK_NOT_FOUND",
                f"No existe la suscripción {subscription_id}",
                hint="Lista webhook.subscription para ver las disponibles.",
            )
        return rows[0]

    async def _event(self, event_id: int) -> dict[str, Any] | None:
        """Relee el evento del outbox para rearmar el cuerpo tal cual era."""
        events = await self.outbox.since(int(event_id) - 1, 1)
        if not events or int(events[0]["id"]) != int(event_id):
            return None
        return events[0]


def _is_success(status: int) -> bool:
    return 200 <= status < 300
