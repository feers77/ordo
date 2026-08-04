"""Del outbox al endpoint: firmar, entregar, reintentar y suspender."""

import json
from typing import Any

import pytest
from modules.webhook.service import WebhookError, WebhookService, sign
from ordo_core.actions import dispatch as run_action
from ordo_core.recordset import RecordSet
from ordo_core.services.outbox import Outbox

pytestmark = pytest.mark.integration

HEX = set("0123456789abcdef")
HOOK_URL = "https://example.test/hook"


class StubTransport:
    """Transporte de mentira: recuerda cada envío y responde siempre lo mismo."""

    def __init__(self, status: int = 200) -> None:
        self.status = status
        self.calls: list[tuple[str, Any, dict[str, str]]] = []

    async def send(self, url: str, body: Any, headers: dict[str, str]) -> int:
        self.calls.append((url, body, headers))
        return self.status


async def emit(
    shop: dict[str, Any],
    event_type: str = "sale.order.action_confirm",
    subject: str = "sale.order/1",
) -> int:
    """Deja un evento en el outbox y lo confirma, como haría una acción real."""
    outbox = Outbox(shop["env"].session)
    event_id = await outbox.emit(event_type, subject, {"result": {"ok": True}})
    await shop["session"].commit()
    return event_id


async def subscribe(
    shop: dict[str, Any],
    pattern: str = "sale.order.*",
    url: str = HOOK_URL,
) -> dict[str, Any]:
    service = WebhookService(shop["env"])
    created = await service.create_subscription(
        name="Integración de pruebas",
        url=url,
        event_pattern=pattern,
        company_id=shop["company_id"],
    )
    await shop["session"].commit()
    return created


async def read_subscription(shop: dict[str, Any], subscription_id: int) -> dict[str, Any]:
    [row] = await RecordSet(shop["env"], "webhook.subscription").read(
        [subscription_id], fields=["state", "failure_count", "secret"]
    )
    return row


async def deliveries(shop: dict[str, Any], subscription_id: int) -> list[dict[str, Any]]:
    """search no admite `order`: la cronología se reconstruye en Python."""
    result = await RecordSet(shop["env"], "webhook.delivery").search(
        [("subscription_id", "=", subscription_id)],
        fields=["event_id", "event_type", "status", "attempts"],
        limit=200,
    )
    return sorted(result["rows"], key=lambda row: row["id"])


class TestSubscription:
    async def test_create_returns_id_and_stores_the_secret(self, shop: dict[str, Any]) -> None:
        """El secreto se devuelve al crear y queda guardado tal cual en la fila."""
        created = await subscribe(shop)
        assert isinstance(created["id"], int)
        assert len(created["secret"]) == 64
        assert set(created["secret"]) <= HEX
        row = await read_subscription(shop, created["id"])
        assert row["secret"] == created["secret"]

    @pytest.mark.parametrize("url", ["ftp://x", "nada"])
    async def test_url_without_http_scheme_is_rejected(
        self, shop: dict[str, Any], url: str
    ) -> None:
        with pytest.raises(WebhookError) as excinfo:
            await subscribe(shop, url=url)
        assert excinfo.value.code == "WEBHOOK_URL_INVALID"

    async def test_empty_pattern_is_rejected(self, shop: dict[str, Any]) -> None:
        """Sin patrón la suscripción no significa nada: mejor no existir."""
        with pytest.raises(WebhookError) as excinfo:
            await subscribe(shop, pattern="")
        assert excinfo.value.code == "WEBHOOK_INVALID_PATTERN"


class TestDispatch:
    async def test_matching_events_are_delivered_signed(self, shop: dict[str, Any]) -> None:
        """Cada evento del patrón sale firmado, con su tenant y su id de entrega."""
        created = await subscribe(shop)
        await emit(shop)
        await emit(shop, subject="sale.order/2")

        transport = StubTransport(200)
        result = await WebhookService(shop["env"]).dispatch_pending(transport)

        assert result == {"delivered": 2, "failed": 0}
        assert len(transport.calls) == 2
        url, body, headers = transport.calls[0]
        assert url == HOOK_URL
        payload = json.loads(body)
        assert payload["event_type"] == "sale.order.action_confirm"
        assert payload["tenant"] == shop["env"].tenant
        assert headers["X-Ordo-Event"] == "sale.order.action_confirm"
        assert headers["X-Ordo-Signature"] == sign(body, created["secret"])
        assert headers["X-Ordo-Delivery"].isdigit()

    async def test_watermark_never_resends_an_old_event(self, shop: dict[str, Any]) -> None:
        """Sin eventos nuevos el barrido es un no-op: la marca de agua no retrocede."""
        await subscribe(shop)
        await emit(shop)
        service = WebhookService(shop["env"])
        transport = StubTransport(200)

        assert await service.dispatch_pending(transport) == {"delivered": 1, "failed": 0}
        assert len(transport.calls) == 1
        assert await service.dispatch_pending(transport) == {"delivered": 0, "failed": 0}
        assert len(transport.calls) == 1

    async def test_events_outside_the_pattern_are_skipped(self, shop: dict[str, Any]) -> None:
        """Lo que no calza deja rastro 'skipped' y avanza la marca, pero no viaja."""
        created = await subscribe(shop, pattern="account.*")
        await emit(shop)
        service = WebhookService(shop["env"])
        transport = StubTransport(200)

        assert await service.dispatch_pending(transport) == {"delivered": 0, "failed": 0}
        assert transport.calls == []
        assert [row["status"] for row in await deliveries(shop, created["id"])] == ["skipped"]

        await emit(shop, event_type="account.move.action_post", subject="account.move/1")
        assert await service.dispatch_pending(transport) == {"delivered": 1, "failed": 0}
        assert len(transport.calls) == 1
        statuses = [row["status"] for row in await deliveries(shop, created["id"])]
        assert statuses == ["skipped", "delivered"]

    async def test_ten_consecutive_failures_suspend_the_subscription(
        self, shop: dict[str, Any]
    ) -> None:
        """Un endpoint muerto se deja de golpear: al décimo fallo, suspensión."""
        created = await subscribe(shop)
        service = WebhookService(shop["env"])
        failing = StubTransport(500)
        for index in range(10):
            await emit(shop, subject=f"sale.order/{index}")
            await service.dispatch_pending(failing)

        assert len(failing.calls) == 10
        row = await read_subscription(shop, created["id"])
        assert row["failure_count"] == 10
        assert row["state"] == "suspended"

        await emit(shop, subject="sale.order/99")
        await service.dispatch_pending(failing)
        assert len(failing.calls) == 10


class TestRetry:
    async def test_failure_is_recorded_and_counted(self, shop: dict[str, Any]) -> None:
        created = await subscribe(shop)
        await emit(shop)

        result = await WebhookService(shop["env"]).dispatch_pending(StubTransport(500))

        assert result == {"delivered": 0, "failed": 1}
        [row] = await deliveries(shop, created["id"])
        assert row["status"] == "failed"
        assert row["attempts"] == 1
        assert (await read_subscription(shop, created["id"]))["failure_count"] == 1

    async def test_retry_reuses_the_delivery_id_and_clears_the_counter(
        self, shop: dict[str, Any]
    ) -> None:
        """El reintento conserva X-Ordo-Delivery: el receptor puede deduplicar."""
        created = await subscribe(shop)
        await emit(shop)
        service = WebhookService(shop["env"])
        failing = StubTransport(500)
        await service.dispatch_pending(failing)

        working = StubTransport(200)
        result = await service.retry_failed(working)

        assert result["delivered"] == 1
        assert len(working.calls) == 1
        assert working.calls[0][2]["X-Ordo-Delivery"] == failing.calls[0][2]["X-Ordo-Delivery"]
        [row] = await deliveries(shop, created["id"])
        assert row["status"] == "delivered"
        assert (await read_subscription(shop, created["id"]))["failure_count"] == 0


class TestActions:
    async def test_suspend_and_resume(self, shop: dict[str, Any]) -> None:
        """Reanudar es un perdón completo: vuelve a activa y sin fallos acumulados."""
        created = await subscribe(shop)
        await emit(shop)
        await WebhookService(shop["env"]).dispatch_pending(StubTransport(500))
        env = shop["env"]

        await run_action(env, "webhook.subscription", "action_suspend", created["id"], {})
        assert (await read_subscription(shop, created["id"]))["state"] == "suspended"

        await run_action(env, "webhook.subscription", "action_resume", created["id"], {})
        row = await read_subscription(shop, created["id"])
        assert row["state"] == "active"
        assert row["failure_count"] == 0
