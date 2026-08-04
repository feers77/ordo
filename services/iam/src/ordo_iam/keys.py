"""Signing keys for ordo-iam issued tokens (F1-03).

Key source, in order: IAM_SIGNING_KEY_FILE (PEM), IAM_SIGNING_KEY_PEM,
else an ephemeral dev key (warns; tokens die with the process).
"""

from __future__ import annotations

import logging
import os
from functools import lru_cache
from pathlib import Path
from typing import Any

from joserfc.jwk import KeySet, RSAKey

logger = logging.getLogger("ordo.iam.keys")

DEFAULT_ISSUER = "http://ordo-iam.internal"


def issuer() -> str:
    return os.environ.get("IAM_ISSUER", DEFAULT_ISSUER)


@lru_cache(maxsize=1)
def signing_key() -> RSAKey:
    path = os.environ.get("IAM_SIGNING_KEY_FILE")
    pem = Path(path).read_bytes() if path else None
    if pem is None:
        env_pem = os.environ.get("IAM_SIGNING_KEY_PEM")
        pem = env_pem.encode() if env_pem else None
    if pem is not None:
        key = RSAKey.import_key(pem)
        if not key.kid:
            key.ensure_kid()
        return key
    logger.warning("IAM sin llave configurada: usando llave efímera de desarrollo")
    return RSAKey.generate_key(2048, {"alg": "RS256", "kid": "dev-ephemeral"})


def public_jwks() -> dict[str, Any]:
    return dict(KeySet([signing_key()]).as_dict(private=False))
