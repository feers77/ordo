"""Configuración del canal de Telegram."""

from __future__ import annotations

import pytest
from ordo_iam import telegram


class TestApiBase:
    """Sin una URL configurable no hay forma de probar el canal sin Internet.

    `TelegramSender` ya aceptaba la URL por constructor, pero el worker la
    fijaba dura, así que solo los tests unitarios podían inyectarla y el flujo
    completo —worker, mensaje, botones firmados, callback— no se podía verificar
    sin un bot de verdad.
    """

    def test_the_default_is_the_real_telegram(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("TELEGRAM_API_BASE", raising=False)
        assert telegram.api_base() == "https://api.telegram.org"

    def test_it_can_point_at_a_local_receiver(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("TELEGRAM_API_BASE", "http://127.0.0.1:8100/desk/tg/")
        assert telegram.api_base() == "http://127.0.0.1:8100/desk/tg"

    def test_the_worker_sender_honours_it(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "123:abc")
        monkeypatch.setenv("TELEGRAM_API_BASE", "http://receptor.local")
        sender = telegram.sender_from_env()
        assert isinstance(sender, telegram.TelegramSender)
        assert sender._base_url == "http://receptor.local"

    def test_a_trailing_slash_does_not_double_up(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """`{base}/bot{token}/sendMessage` con base terminada en barra daría
        una URL con doble barra que algunos receptores rechazan."""
        monkeypatch.setenv("TELEGRAM_API_BASE", "http://receptor.local/")
        assert not telegram.api_base().endswith("/")
