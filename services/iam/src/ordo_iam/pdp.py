"""Policy Decision Point (F1-05, PLAN §2.5).

Layer 1: capability claims (agents) — deny by default, evaluated first.
Layer 2: RBAC (roles + ACL per model/op) on the effective user.
Layer 3: record rules — returned as domains for the kernel to compile (F2).

Money is Decimal end to end; daily accumulators are integer micros in the
usage counter (never float, AGENTS.md §2.3).
"""

from __future__ import annotations

import os
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal
from fnmatch import fnmatch
from typing import Any, Protocol

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ordo_iam.captokens import Cap
from ordo_iam.models import Acl, RecordRule, RoleMember

MICROS = 1_000_000
DAY_TTL_S = 25 * 3600

READ_OPS = {"read"}
CRUD_OPS = {"read", "write", "create", "unlink"}


class UsageCounter(Protocol):
    async def incr_and_get(self, key: str, amount_micros: int, ttl_s: int) -> int:
        """Atomically add and return the new total for the key."""
        ...


class InMemoryUsageCounter:
    """For tests and single-process dev."""

    def __init__(self) -> None:
        self._data: dict[str, int] = {}

    async def incr_and_get(self, key: str, amount_micros: int, ttl_s: int) -> int:
        self._data[key] = self._data.get(key, 0) + amount_micros
        return self._data[key]


class RedisUsageCounter:
    def __init__(self, url: str | None = None) -> None:
        import redis.asyncio as aioredis

        self._redis = aioredis.from_url(
            url or os.environ.get("REDIS_URL", "redis://127.0.0.1:6379/0")
        )

    async def incr_and_get(self, key: str, amount_micros: int, ttl_s: int) -> int:
        total = await self._redis.incrby(key, amount_micros)
        await self._redis.expire(key, ttl_s, nx=True)
        return int(total)


@dataclass(frozen=True)
class Amount:
    currency: str
    value: Decimal

    def __post_init__(self) -> None:
        if not isinstance(self.value, Decimal):
            # defensa runtime contra floats (AGENTS.md §2.3); mypy lo ve imposible
            msg = "Amount.value debe ser Decimal (nunca float)"  # type: ignore[unreachable]
            raise TypeError(msg)

    @property
    def micros(self) -> int:
        return int(self.value * MICROS)


@dataclass(frozen=True)
class AccessRequest:
    tenant: str
    model: str
    operation: str
    amount: Amount | None = None
    agent_id: str | None = None
    user_id: uuid.UUID | None = None


@dataclass(frozen=True)
class Decision:
    allowed: bool
    reason: str = "OK"
    requires_approval: bool = False
    record_domain: dict[str, Any] = field(default_factory=dict)


def _matches(patterns: list[str], target: str) -> bool:
    return any(fnmatch(target, pattern) for pattern in patterns)


class CapabilityChecker:
    def __init__(self, counter: UsageCounter) -> None:
        self.counter = counter

    async def check(self, cap: Cap | None, req: AccessRequest) -> Decision:
        if cap is None:
            return Decision(False, "CAP_NOT_GRANTED")
        target = f"{req.model}.{req.operation}"

        if _matches(cap.get("deny", []), target) or _matches(cap.get("deny", []), req.model):
            return Decision(False, "CAP_DENIED")

        granted_ops = set(cap.get("models", {}).get(req.model, []))
        is_crud = req.operation in CRUD_OPS
        # métodos de negocio (action_*) requieren 'write' sobre el modelo
        effective_op = req.operation if is_crud else "write"
        if effective_op not in granted_ops:
            return Decision(False, "CAP_NOT_GRANTED")

        limits = cap.get("limits", {})
        if req.amount is not None:
            per_op = limits.get("max_amount_per_op", {}).get(req.amount.currency)
            if per_op is not None and req.amount.value > Decimal(str(per_op)):
                return Decision(False, "CAP_AMOUNT_EXCEEDED")
            per_day = limits.get("max_amount_per_day", {}).get(req.amount.currency)
            if per_day is not None:
                key = self._day_key(req)
                try:
                    total = await self.counter.incr_and_get(key, req.amount.micros, DAY_TTL_S)
                except Exception:
                    return Decision(False, "CAP_LIMIT_BACKEND_DOWN")
                if total > int(Decimal(str(per_day)) * MICROS):
                    # revierte lo sumado: la operación no se ejecutará
                    await self._safe_decr(key, req.amount.micros)
                    return Decision(False, "CAP_DAILY_LIMIT")

        requires_approval = _matches(cap.get("requires_approval", []), target)
        return Decision(True, "OK", requires_approval=requires_approval)

    def _day_key(self, req: AccessRequest) -> str:
        assert req.amount is not None
        day = datetime.now(UTC).strftime("%Y%m%d")
        return f"iam:limits:day:{req.tenant}:{req.agent_id}:{req.amount.currency}:{day}"

    async def _safe_decr(self, key: str, micros: int) -> None:
        import contextlib

        with contextlib.suppress(Exception):
            await self.counter.incr_and_get(key, -micros, DAY_TTL_S)


class PolicyEngine:
    """Full three-layer evaluation. Needs a DB session for RBAC/rules."""

    def __init__(self, session: AsyncSession, counter: UsageCounter) -> None:
        self.session = session
        self.caps = CapabilityChecker(counter)

    async def evaluate(self, req: AccessRequest, *, cap: Cap | None) -> Decision:
        requires_approval = False
        if req.agent_id is not None:
            cap_decision = await self.caps.check(cap, req)
            if not cap_decision.allowed:
                return cap_decision
            requires_approval = cap_decision.requires_approval

        if req.user_id is None:
            return Decision(False, "RBAC_DENIED")
        if not await self._rbac_allows(req):
            return Decision(False, "RBAC_DENIED")

        domains = await self._record_domains(req)
        return Decision(True, "OK", requires_approval=requires_approval, record_domain=domains)

    async def _rbac_allows(self, req: AccessRequest) -> bool:
        op = req.operation if req.operation in CRUD_OPS else "write"
        column = {
            "read": Acl.perm_read,
            "write": Acl.perm_write,
            "create": Acl.perm_create,
            "unlink": Acl.perm_unlink,
        }[op]
        row = await self.session.scalar(
            select(Acl.id)
            .join(RoleMember, RoleMember.role_id == Acl.role_id)
            .where(
                RoleMember.principal_id == req.user_id,
                Acl.model == req.model,
                column.is_(True),
            )
            .limit(1)
        )
        return row is not None

    async def _record_domains(self, req: AccessRequest) -> dict[str, Any]:
        op = req.operation if req.operation in CRUD_OPS else "write"
        rules = (
            await self.session.scalars(
                select(RecordRule).where(
                    RecordRule.tenant == req.tenant,
                    RecordRule.model == req.model,
                    RecordRule.ops.contains([op]),
                )
            )
        ).all()
        global_and: list[Any] = []
        role_or: list[Any] = []
        member_role_ids = set(
            (
                await self.session.scalars(
                    select(RoleMember.role_id).where(RoleMember.principal_id == req.user_id)
                )
            ).all()
        )
        for rule in rules:
            if rule.role_id is None:
                global_and.append(rule.domain)
            elif rule.role_id in member_role_ids:
                role_or.append(rule.domain)
        out: dict[str, Any] = {}
        if global_and:
            out["global_and"] = global_and
        if role_or:
            out["role_or"] = role_or
        return out
