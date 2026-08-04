"""Capability merging: N effective grants → one `cap` claim (ADR-004, F1-03).

Grants are additive between themselves; restriction against the delegating
user's own permissions happens at PDP evaluation time, never here.
"""

from __future__ import annotations

from typing import Any

Cap = dict[str, Any]


def merge_caps(caps: list[Cap]) -> Cap | None:
    if not caps:
        return None

    models: dict[str, set[str]] = {}
    deny: set[str] = set()
    requires_approval: set[str] = set()
    amount_limits: dict[str, dict[str, float]] = {}
    scalar_limits: dict[str, float] = {}
    record_domain: list[Any] = []

    for cap in caps:
        for model, ops in cap.get("models", {}).items():
            models.setdefault(model, set()).update(ops)
        deny.update(cap.get("deny", []))
        requires_approval.update(cap.get("requires_approval", []))
        limits = cap.get("limits", {})
        for key, value in limits.items():
            if key == "record_domain":
                record_domain.extend(value)
            elif isinstance(value, dict):
                bucket = amount_limits.setdefault(key, {})
                for currency, amount in value.items():
                    bucket[currency] = min(bucket.get(currency, amount), amount)
            else:
                scalar_limits[key] = min(scalar_limits.get(key, value), value)

    merged: Cap = {"models": {m: sorted(ops) for m, ops in sorted(models.items())}}
    if deny:
        merged["deny"] = sorted(deny)
    if requires_approval:
        merged["requires_approval"] = sorted(requires_approval)
    limits_out: dict[str, Any] = dict(sorted(scalar_limits.items()))
    limits_out.update({k: dict(sorted(v.items())) for k, v in sorted(amount_limits.items())})
    if record_domain:
        limits_out["record_domain"] = record_domain
    if limits_out:
        merged["limits"] = limits_out
    return merged
