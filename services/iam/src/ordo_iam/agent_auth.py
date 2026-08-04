"""Agent credential generation and verification (F1-03).

Secrets are 32 random bytes (not human passwords): salted SHA-256 with
constant-time comparison is appropriate here.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets

from ordo_iam.models import Agent


def generate_secret() -> str:
    return secrets.token_urlsafe(32)


def hash_secret(secret: str, salt: str) -> str:
    return hashlib.sha256(f"{salt}:{secret}".encode()).hexdigest()


def new_credentials() -> tuple[str, str, str]:
    """Return (secret, salt, hash)."""
    secret = generate_secret()
    salt = secrets.token_hex(16)
    return secret, salt, hash_secret(secret, salt)


def verify_secret(agent: Agent, secret: str) -> bool:
    if not agent.secret_hash or not agent.secret_salt:
        return False
    expected = hash_secret(secret, agent.secret_salt)
    return hmac.compare_digest(expected, agent.secret_hash)
