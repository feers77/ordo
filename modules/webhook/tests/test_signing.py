"""La firma HMAC y el patrón de eventos: pura aritmética, sin base de datos."""

import hashlib
import hmac

from modules.webhook.service import generate_secret, pattern_matches, sign

HEX = set("0123456789abcdef")
BODY = b'{"event_id": 7, "event_type": "sale.order.action_confirm"}'
SECRET = "a" * 64
EVENT = "sale.order.action_confirm"


class TestSign:
    def test_shape_is_prefix_plus_sha256_hex(self) -> None:
        """La firma es 'sha256=' seguido de 64 hexadecimales."""
        signature = sign(BODY, SECRET)
        assert signature.startswith("sha256=")
        digest = signature.removeprefix("sha256=")
        assert len(digest) == 64
        assert set(digest) <= HEX

    def test_is_deterministic(self) -> None:
        assert sign(BODY, SECRET) == sign(BODY, SECRET)

    def test_changes_when_the_body_changes(self) -> None:
        """Un byte distinto en el cuerpo invalida la firma: no hay maleabilidad."""
        assert sign(BODY, SECRET) != sign(BODY + b" ", SECRET)

    def test_changes_when_the_secret_changes(self) -> None:
        assert sign(BODY, SECRET) != sign(BODY, "b" * 64)

    def test_matches_a_manual_verification(self) -> None:
        """El receptor verifica con la librería estándar y nada más."""
        expected = hmac.new(SECRET.encode(), BODY, hashlib.sha256).hexdigest()
        assert sign(BODY, SECRET) == f"sha256={expected}"


class TestPatternMatches:
    def test_namespace_wildcard_matches(self) -> None:
        assert pattern_matches(EVENT, "sale.order.*")

    def test_global_wildcard_matches_everything(self) -> None:
        assert pattern_matches(EVENT, "*")

    def test_another_namespace_does_not_match(self) -> None:
        assert not pattern_matches(EVENT, "account.*")

    def test_exact_event_type_matches(self) -> None:
        assert pattern_matches(EVENT, EVENT)


class TestGenerateSecret:
    def test_is_sixty_four_hex_chars(self) -> None:
        secret = generate_secret()
        assert len(secret) == 64
        assert set(secret) <= HEX

    def test_two_calls_never_repeat(self) -> None:
        """Cada suscripción nace con su propio secreto."""
        assert generate_secret() != generate_secret()
