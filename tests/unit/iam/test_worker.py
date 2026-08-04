"""El worker de notificaciones arranca con la app y se apaga con ella."""

import asyncio

import pytest
from fastapi import FastAPI
from ordo_iam import worker as worker_module
from ordo_iam.worker import install_worker, worker_enabled


class TestEnabled:
    def test_off_without_telegram_token(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
        assert not worker_enabled()

    def test_on_with_token(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "x")
        monkeypatch.delenv("ORDO_NOTIFY_WORKER", raising=False)
        assert worker_enabled()

    def test_explicit_kill_switch_wins(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "x")
        monkeypatch.setenv("ORDO_NOTIFY_WORKER", "0")
        assert not worker_enabled()


class TestLifecycle:
    async def test_worker_starts_and_stops_with_the_app(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "x")
        monkeypatch.delenv("ORDO_NOTIFY_WORKER", raising=False)
        started = asyncio.Event()
        cancelled = asyncio.Event()

        async def fake_loop() -> None:
            started.set()
            try:
                await asyncio.sleep(3600)
            except asyncio.CancelledError:
                cancelled.set()
                raise

        monkeypatch.setattr(worker_module, "worker_loop", fake_loop)
        app = FastAPI()
        install_worker(app)

        async with app.router.lifespan_context(app):
            await asyncio.wait_for(started.wait(), timeout=1)
        await asyncio.wait_for(cancelled.wait(), timeout=1)

    async def test_worker_absent_when_disabled(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
        called = False

        async def fake_loop() -> None:  # pragma: no cover - no debe ejecutarse
            nonlocal called
            called = True

        monkeypatch.setattr(worker_module, "worker_loop", fake_loop)
        app = FastAPI()
        install_worker(app)
        async with app.router.lifespan_context(app):
            await asyncio.sleep(0)
        assert not called
