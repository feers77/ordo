"""Tests del motor PDP (F1-05) — escritos ANTES de implementar.

La capa RBAC/reglas se prueba en integración; aquí la lógica pura de
capabilities y composición, con contador de uso en memoria.
"""

from decimal import Decimal

import pytest
from ordo_iam.pdp import (
    AccessRequest,
    Amount,
    CapabilityChecker,
    InMemoryUsageCounter,
)

CAP = {
    "models": {
        "sale.order": ["read", "create", "write"],
        "account.move": ["read"],
    },
    "limits": {
        "max_amount_per_op": {"CLP": 5_000_000},
        "max_amount_per_day": {"CLP": 8_000_000},
    },
    "requires_approval": ["account.move.action_post", "res.partner.unlink"],
    "deny": ["res.users.*", "ir.model.*"],
}


def req(
    model: str = "sale.order",
    operation: str = "read",
    amount: Amount | None = None,
) -> AccessRequest:
    return AccessRequest(
        tenant="acme",
        model=model,
        operation=operation,
        amount=amount,
        agent_id="agent-1",
    )


def checker(counter: InMemoryUsageCounter | None = None) -> CapabilityChecker:
    return CapabilityChecker(counter or InMemoryUsageCounter())


class TestCapabilityLayer:
    async def test_allowed_op_passes(self) -> None:
        result = await checker().check(CAP, req("sale.order", "read"))
        assert result.allowed

    async def test_model_not_granted_denied(self) -> None:
        result = await checker().check(CAP, req("stock.picking", "read"))
        assert not result.allowed
        assert result.reason == "CAP_NOT_GRANTED"

    async def test_op_not_granted_denied(self) -> None:
        result = await checker().check(CAP, req("account.move", "write"))
        assert not result.allowed
        assert result.reason == "CAP_NOT_GRANTED"

    async def test_deny_glob_wins_over_grant(self) -> None:
        cap = dict(CAP, models={"res.users": ["write"], **CAP["models"]})
        result = await checker().check(cap, req("res.users", "write"))
        assert not result.allowed
        assert result.reason == "CAP_DENIED"

    async def test_deny_wildcard_model(self) -> None:
        result = await checker().check(CAP, req("ir.model.fields", "read"))
        assert not result.allowed
        assert result.reason == "CAP_DENIED"

    async def test_amount_within_limit_passes(self) -> None:
        result = await checker().check(
            CAP, req("sale.order", "create", Amount("CLP", Decimal("4999999")))
        )
        assert result.allowed

    async def test_amount_per_op_exceeded(self) -> None:
        result = await checker().check(
            CAP, req("sale.order", "create", Amount("CLP", Decimal("5000001")))
        )
        assert not result.allowed
        assert result.reason == "CAP_AMOUNT_EXCEEDED"

    async def test_daily_limit_accumulates(self) -> None:
        counter = InMemoryUsageCounter()
        c = checker(counter)
        first = await c.check(CAP, req("sale.order", "create", Amount("CLP", Decimal("5000000"))))
        assert first.allowed
        second = await c.check(CAP, req("sale.order", "create", Amount("CLP", Decimal("3000001"))))
        assert not second.allowed
        assert second.reason == "CAP_DAILY_LIMIT"

    async def test_denied_op_does_not_consume_daily_budget(self) -> None:
        counter = InMemoryUsageCounter()
        c = checker(counter)
        big = await c.check(CAP, req("sale.order", "create", Amount("CLP", Decimal("6000000"))))
        assert not big.allowed
        ok = await c.check(CAP, req("sale.order", "create", Amount("CLP", Decimal("8000000"))))
        assert not ok.allowed  # supera per-op igual
        fine = await c.check(CAP, req("sale.order", "create", Amount("CLP", Decimal("4000000"))))
        assert fine.allowed

    async def test_currency_without_limit_passes(self) -> None:
        result = await checker().check(
            CAP, req("sale.order", "create", Amount("USD", Decimal("999999999")))
        )
        assert result.allowed

    async def test_requires_approval_flagged(self) -> None:
        result = await checker().check(CAP, req("account.move", "action_post"))
        # action_post no está en models pero sí en requires_approval:
        # la operación method-level se evalúa como write implícito del modelo
        assert result.requires_approval or not result.allowed

    async def test_requires_approval_on_granted_op(self) -> None:
        cap = {
            "models": {"res.partner": ["read", "unlink"]},
            "requires_approval": ["res.partner.unlink"],
        }
        result = await checker().check(cap, req("res.partner", "unlink"))
        assert result.allowed
        assert result.requires_approval

    async def test_counter_failure_fails_closed(self) -> None:
        class BrokenCounter(InMemoryUsageCounter):
            async def incr_and_get(self, key: str, amount_micros: int, ttl_s: int) -> int:
                msg = "redis caído"
                raise ConnectionError(msg)

        result = await checker(BrokenCounter()).check(
            CAP, req("sale.order", "create", Amount("CLP", Decimal("1")))
        )
        assert not result.allowed
        assert result.reason == "CAP_LIMIT_BACKEND_DOWN"

    async def test_no_cap_denies(self) -> None:
        result = await checker().check(None, req())
        assert not result.allowed
        assert result.reason == "CAP_NOT_GRANTED"


class TestAmountMicros:
    def test_decimal_to_micros_exact(self) -> None:
        assert Amount("CLP", Decimal("1234.56")).micros == 1_234_560_000

    def test_no_float_anywhere(self) -> None:
        amount = Amount("CLP", Decimal("0.1"))
        assert amount.micros == 100_000
        with pytest.raises(TypeError):
            Amount("CLP", 0.1)  # type: ignore[arg-type]
