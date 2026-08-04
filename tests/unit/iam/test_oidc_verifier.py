"""Tests de seguridad del verificador OIDC (F1-02) — escritos ANTES de implementar.

Sin red: JWKS generado localmente simulando el emisor.
"""

import time
from typing import Any

import pytest
from joserfc import jwt
from joserfc.jwk import KeySet, OctKey, RSAKey
from ordo_iam.errors import TokenExpiredError, TokenInvalidError
from ordo_iam.oidc import OIDCVerifier

ISSUER = "http://idp.test/realms/ordo"
AUDIENCE = "ordo-api"

KEY = RSAKey.generate_key(2048, {"kid": "good-key", "alg": "RS256"})
OTHER_KEY = RSAKey.generate_key(2048, {"kid": "evil-key", "alg": "RS256"})
JWKS = KeySet([KEY])


def make_claims(**overrides: Any) -> dict[str, Any]:
    now = int(time.time())
    claims: dict[str, Any] = {
        "iss": ISSUER,
        "aud": AUDIENCE,
        "sub": "kc-user-1",
        "iat": now,
        "exp": now + 300,
        "email": "ana@acme.cl",
        "email_verified": True,
        "tenant": "acme",
    }
    claims.update(overrides)
    return {k: v for k, v in claims.items() if v is not None}


def sign(claims: dict[str, Any], key: RSAKey = KEY, alg: str = "RS256") -> str:
    return jwt.encode({"alg": alg, "kid": key.kid}, claims, key)


def make_verifier() -> OIDCVerifier:
    return OIDCVerifier(issuer=ISSUER, audience=AUDIENCE, static_jwks=JWKS)


class TestOIDCVerifier:
    def test_valid_token_passes(self) -> None:
        claims = make_verifier().verify(sign(make_claims()))
        assert claims["sub"] == "kc-user-1"
        assert claims["tenant"] == "acme"

    def test_expired_token_rejected(self) -> None:
        token = sign(make_claims(exp=int(time.time()) - 10))
        with pytest.raises(TokenExpiredError) as exc:
            make_verifier().verify(token)
        assert exc.value.code == "IAM_TOKEN_EXPIRED"
        assert exc.value.retryable is True

    def test_wrong_issuer_rejected(self) -> None:
        token = sign(make_claims(iss="http://evil.test/realms/ordo"))
        with pytest.raises(TokenInvalidError):
            make_verifier().verify(token)

    def test_wrong_audience_rejected(self) -> None:
        token = sign(make_claims(aud="otra-api"))
        with pytest.raises(TokenInvalidError):
            make_verifier().verify(token)

    def test_alg_none_rejected(self) -> None:
        # token alg=none construido a mano (sin firma)
        import base64
        import json

        header = base64.urlsafe_b64encode(json.dumps({"alg": "none"}).encode()).rstrip(b"=")
        payload = base64.urlsafe_b64encode(json.dumps(make_claims()).encode()).rstrip(b"=")
        token = header.decode() + "." + payload.decode() + "."
        with pytest.raises(TokenInvalidError):
            make_verifier().verify(token)

    def test_symmetric_key_confusion_rejected(self) -> None:
        # HS256 firmado usando material público como secreto compartido
        pem = KEY.as_pem(private=False)
        oct_key = OctKey.import_key(pem)
        token = jwt.encode({"alg": "HS256", "kid": KEY.kid}, make_claims(), oct_key)
        with pytest.raises(TokenInvalidError):
            make_verifier().verify(token)

    def test_unknown_kid_rejected(self) -> None:
        token = sign(make_claims(), key=OTHER_KEY)
        with pytest.raises(TokenInvalidError):
            make_verifier().verify(token)

    def test_tampered_signature_rejected(self) -> None:
        token = sign(make_claims())
        head, payload, sig = token.split(".")
        tampered = f"{head}.{payload}.{'A' * len(sig)}"
        with pytest.raises(TokenInvalidError):
            make_verifier().verify(tampered)

    def test_tampered_payload_rejected(self) -> None:
        import base64
        import json

        token = sign(make_claims())
        head, _, sig = token.split(".")
        evil = base64.urlsafe_b64encode(json.dumps(make_claims(sub="kc-admin")).encode()).rstrip(
            b"="
        )
        with pytest.raises(TokenInvalidError):
            make_verifier().verify(f"{head}.{evil.decode()}.{sig}")

    def test_missing_sub_rejected(self) -> None:
        token = sign(make_claims(sub=None))
        with pytest.raises(TokenInvalidError):
            make_verifier().verify(token)

    def test_missing_tenant_rejected(self) -> None:
        token = sign(make_claims(tenant=None))
        with pytest.raises(TokenInvalidError):
            make_verifier().verify(token)

    def test_garbage_token_rejected(self) -> None:
        with pytest.raises(TokenInvalidError):
            make_verifier().verify("no.es.jwt")
