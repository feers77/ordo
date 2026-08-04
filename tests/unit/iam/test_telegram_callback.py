"""Firma de los callbacks de Telegram (F1-07) — escritos antes de la implementación.

El callback_data viaja por la red de un tercero y vuelve sin autenticación de
usuario: si no va firmado, cualquiera fabrica una aprobación.
"""

import uuid

import pytest
from ordo_iam.errors import CallbackInvalidError, TelegramNotConfiguredError
from ordo_iam.telegram import (
    TELEGRAM_CALLBACK_MAX_BYTES,
    normalize_link_code,
    sign_callback,
    verify_callback,
)

SECRET = "secreto-de-prueba"


@pytest.fixture(autouse=True)
def _webhook_secret(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TELEGRAM_WEBHOOK_SECRET", SECRET)


class TestCallbackSignature:
    def test_roundtrip_preserves_approval_and_action(self) -> None:
        approval_id = uuid.uuid4()
        assert verify_callback(sign_callback(approval_id, approve=True)) == (approval_id, True)
        assert verify_callback(sign_callback(approval_id, approve=False)) == (approval_id, False)

    def test_fits_the_64_byte_limit_of_telegram(self) -> None:
        data = sign_callback(uuid.uuid4(), approve=True)
        assert len(data.encode()) <= TELEGRAM_CALLBACK_MAX_BYTES

    def test_flipping_the_action_invalidates_the_signature(self) -> None:
        data = sign_callback(uuid.uuid4(), approve=False)
        prefix, approval_hex, _action, signature = data.split(":")
        forged = ":".join([prefix, approval_hex, "a", signature])
        with pytest.raises(CallbackInvalidError):
            verify_callback(forged)

    def test_swapping_the_approval_id_invalidates_the_signature(self) -> None:
        data = sign_callback(uuid.uuid4(), approve=True)
        prefix, _approval_hex, action, signature = data.split(":")
        forged = ":".join([prefix, uuid.uuid4().hex, action, signature])
        with pytest.raises(CallbackInvalidError):
            verify_callback(forged)

    def test_tampered_signature_rejected(self) -> None:
        data = sign_callback(uuid.uuid4(), approve=True)
        forged = data[:-1] + ("0" if data[-1] != "0" else "1")
        with pytest.raises(CallbackInvalidError):
            verify_callback(forged)

    def test_signature_from_another_server_secret_rejected(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        data = sign_callback(uuid.uuid4(), approve=True)
        monkeypatch.setenv("TELEGRAM_WEBHOOK_SECRET", "otro-secreto")
        with pytest.raises(CallbackInvalidError):
            verify_callback(data)

    @pytest.mark.parametrize(
        "data",
        ["", "a1", "a1:x:a:ff", "a1:abc:a", "otro:formato:a:ff", "::::", "a1::a:ff"],
    )
    def test_malformed_callback_rejected(self, data: str) -> None:
        with pytest.raises(CallbackInvalidError):
            verify_callback(data)

    def test_signing_without_configured_secret_fails_closed(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("TELEGRAM_WEBHOOK_SECRET", raising=False)
        with pytest.raises(TelegramNotConfiguredError):
            sign_callback(uuid.uuid4(), approve=True)
        with pytest.raises(TelegramNotConfiguredError):
            verify_callback(f"a1:{uuid.uuid4().hex}:a:0123456789abcdef0123")


class TestLinkCodeNormalization:
    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            ("/start ABCDE-FGHJK", "ABCDEFGHJK"),
            ("abcde fghjk", "ABCDEFGHJK"),
            ("  ABCDEFGHJK\n", "ABCDEFGHJK"),
            ("/start@un_bot abcde-fghjk", "ABCDEFGHJK"),
        ],
    )
    def test_user_typed_variants_normalize_to_the_same_code(self, raw: str, expected: str) -> None:
        assert normalize_link_code(raw) == expected

    def test_plain_text_that_is_not_a_code_returns_empty(self) -> None:
        assert normalize_link_code("hola, ¿qué tal?") == ""
