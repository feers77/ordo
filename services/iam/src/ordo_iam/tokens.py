"""Issuance of agent access tokens (capability tokens, ADR-004)."""

from __future__ import annotations

import time
import uuid
from typing import Any

from joserfc import jwt

from ordo_iam.captokens import Cap
from ordo_iam.keys import issuer, signing_key

AGENT_TOKEN_TTL_S = 900

WRITE_OPS = {"create", "write", "unlink"}


def derive_scope(cap: Cap) -> str:
    ops = {op for ops in cap.get("models", {}).values() for op in ops}
    scopes = ["erp.read"]
    if ops & WRITE_OPS:
        scopes.append("erp.write")
    return " ".join(scopes)


def issue_agent_token(
    *,
    agent_id: uuid.UUID,
    acting_for_user_id: uuid.UUID,
    tenant: str,
    cap: Cap,
) -> tuple[str, dict[str, Any]]:
    """Sign and return (token, claims)."""
    now = int(time.time())
    claims: dict[str, Any] = {
        "iss": issuer(),
        "sub": f"agent:{agent_id}",
        "act": {"sub": f"user:{acting_for_user_id}"},
        "aud": "ordo-api",
        "tenant": tenant,
        "scope": derive_scope(cap),
        "cap": cap,
        "iat": now,
        "exp": now + AGENT_TOKEN_TTL_S,
        "jti": uuid.uuid4().hex,
    }
    key = signing_key()
    token = jwt.encode({"alg": "RS256", "kid": key.kid}, claims, key)
    return token, claims
