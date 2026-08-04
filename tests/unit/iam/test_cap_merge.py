"""Tests del merge de capability grants (F1-03/ADR-004) — antes de implementar."""

from ordo_iam.captokens import merge_caps


class TestMergeCaps:
    def test_empty_grants_yields_none(self) -> None:
        assert merge_caps([]) is None

    def test_single_grant_passthrough(self) -> None:
        cap = {"models": {"sale.order": ["read", "create"]}}
        # salida canónica: operaciones ordenadas
        assert merge_caps([cap]) == {"models": {"sale.order": ["create", "read"]}}

    def test_models_union(self) -> None:
        merged = merge_caps(
            [
                {"models": {"sale.order": ["read"], "res.partner": ["read"]}},
                {"models": {"sale.order": ["create", "read"]}},
            ]
        )
        assert merged is not None
        assert sorted(merged["models"]["sale.order"]) == ["create", "read"]
        assert merged["models"]["res.partner"] == ["read"]

    def test_limits_take_most_restrictive(self) -> None:
        merged = merge_caps(
            [
                {
                    "models": {"sale.order": ["read"]},
                    "limits": {
                        "max_amount_per_op": {"CLP": 5_000_000},
                        "max_writes_per_min": 120,
                    },
                },
                {
                    "models": {"sale.order": ["read"]},
                    "limits": {
                        "max_amount_per_op": {"CLP": 1_000_000, "USD": 500},
                        "max_writes_per_min": 60,
                    },
                },
            ]
        )
        assert merged is not None
        assert merged["limits"]["max_amount_per_op"] == {"CLP": 1_000_000, "USD": 500}
        assert merged["limits"]["max_writes_per_min"] == 60

    def test_deny_and_requires_approval_union(self) -> None:
        merged = merge_caps(
            [
                {
                    "models": {"account.move": ["read"]},
                    "deny": ["res.users.write"],
                    "requires_approval": ["account.move.action_post"],
                },
                {
                    "models": {"account.move": ["read"]},
                    "deny": ["ir.model.*"],
                    "requires_approval": ["res.partner.unlink"],
                },
            ]
        )
        assert merged is not None
        assert sorted(merged["deny"]) == ["ir.model.*", "res.users.write"]
        assert sorted(merged["requires_approval"]) == [
            "account.move.action_post",
            "res.partner.unlink",
        ]

    def test_record_domains_concatenate_as_and(self) -> None:
        merged = merge_caps(
            [
                {
                    "models": {"sale.order": ["read"]},
                    "limits": {"record_domain": [["company_id", "in", [1, 3]]]},
                },
                {
                    "models": {"sale.order": ["read"]},
                    "limits": {"record_domain": [["state", "=", "sale"]]},
                },
            ]
        )
        assert merged is not None
        assert merged["limits"]["record_domain"] == [
            ["company_id", "in", [1, 3]],
            ["state", "=", "sale"],
        ]

    def test_merge_never_invents_keys(self) -> None:
        merged = merge_caps([{"models": {"x": ["read"]}}])
        assert merged is not None
        assert "limits" not in merged
        assert "deny" not in merged
