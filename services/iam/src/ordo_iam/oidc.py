"""Generic OIDC token verifier (design F1-02, ADR-003).

Keycloak is just the current issuer; anything OIDC-compliant works,
which keeps the F3 swap to ordo-iam-as-OP transparent.
"""

from __future__ import annotations

import time
from typing import Any

import httpx
from joserfc import jwt
from joserfc.errors import ExpiredTokenError, JoseError
from joserfc.jwk import KeySet

from ordo_iam.errors import TokenExpiredError, TokenInvalidError

ALLOWED_ALGORITHMS = ["RS256", "ES256"]
JWKS_TTL_S = 300.0
REQUIRED_CLAIMS = ("iss", "aud", "exp", "iat", "sub", "tenant")


class OIDCVerifier:
    def __init__(
        self,
        *,
        issuer: str,
        audience: str,
        jwks_url: str | None = None,
        static_jwks: KeySet | None = None,
    ) -> None:
        self.issuer = issuer
        self.audience = audience
        self.jwks_url = jwks_url or f"{issuer.rstrip('/')}/protocol/openid-connect/certs"
        self._static_jwks = static_jwks
        self._jwks: KeySet | None = static_jwks
        self._fetched_at = 0.0

    def _fetch_jwks(self) -> KeySet:
        response = httpx.get(self.jwks_url, timeout=5.0)
        response.raise_for_status()
        self._fetched_at = time.monotonic()
        return KeySet.import_key_set(response.json())

    def _keyset(self, *, force: bool = False) -> KeySet:
        if self._static_jwks is not None:
            return self._static_jwks
        stale = time.monotonic() - self._fetched_at > JWKS_TTL_S
        if self._jwks is None or stale or force:
            self._jwks = self._fetch_jwks()
        return self._jwks

    def verify(self, token: str) -> dict[str, Any]:
        """Verify signature and claims; return the claim set or raise IAM_TOKEN_*."""
        try:
            decoded = self._decode(token)
        except ExpiredTokenError as exc:
            raise TokenExpiredError(
                "El token expiró.",
                hint="Renueva el token (refresh) y reintenta.",
            ) from exc
        except JoseError as exc:
            raise TokenInvalidError(
                "Token inválido.",
                hint="Verifica emisor, audiencia, algoritmo y firma.",
            ) from exc

        claims: dict[str, Any] = dict(decoded.claims)
        for name in REQUIRED_CLAIMS:
            if not claims.get(name):
                raise TokenInvalidError(
                    f"Falta el claim obligatorio '{name}'.",
                    hint="El emisor debe incluir iss, aud, exp, iat, sub y tenant.",
                )
        return claims

    def _decode(self, token: str) -> jwt.Token:
        registry = jwt.JWTClaimsRegistry(
            iss={"essential": True, "value": self.issuer},
            aud={"essential": True, "value": self.audience},
            exp={"essential": True},
        )
        try:
            decoded = jwt.decode(token, self._keyset(), algorithms=ALLOWED_ALGORITHMS)
        except (JoseError, ValueError) as first_error:
            if self._static_jwks is not None:
                if isinstance(first_error, JoseError):
                    raise
                raise TokenInvalidError("Token malformado.") from first_error
            # kid desconocido: puede haber rotación de llaves → un refetch
            try:
                decoded = jwt.decode(token, self._keyset(force=True), algorithms=ALLOWED_ALGORITHMS)
            except (JoseError, ValueError) as exc:
                if isinstance(exc, JoseError):
                    raise
                raise TokenInvalidError("Token malformado.") from exc
        registry.validate(decoded.claims)
        return decoded
