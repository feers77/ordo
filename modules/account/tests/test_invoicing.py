"""El asiento de una factura cuadra por construcción: probado con hypothesis."""

from decimal import Decimal

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from modules.account.invoicing import ResolvedLine, build_invoice_lines, compute_totals
from modules.account.services import AccountingError, validate_lines
from modules.account.taxes import Tax

IVA19 = Tax(code="IVA19", name="IVA 19%", amount=Decimal("19"))
RET10 = Tax(code="RET10", name="Retención 10%", amount=Decimal("10"), is_withholding=True)

TAX_ROWS = {
    "IVA19": ({"account_id": 61, "code": "IVA19"}, IVA19),
    "RET10": ({"account_id": 62, "code": "RET10"}, RET10),
}

prices = st.decimals(
    min_value=Decimal("0.01"),
    max_value=Decimal("9999999"),
    places=2,
    allow_nan=False,
    allow_infinity=False,
)
quantities = st.decimals(
    min_value=Decimal("0.01"),
    max_value=Decimal("1000"),
    places=2,
    allow_nan=False,
    allow_infinity=False,
)
discounts = st.decimals(
    min_value=Decimal("0"),
    max_value=Decimal("99"),
    places=1,
    allow_nan=False,
    allow_infinity=False,
)


def line(
    price: Decimal,
    quantity: Decimal = Decimal("1"),
    discount: Decimal = Decimal("0"),
    taxes: list[Tax] | None = None,
) -> ResolvedLine:
    return ResolvedLine(
        name="línea",
        quantity=quantity,
        price_unit=price,
        discount_percent=discount,
        taxes=taxes if taxes is not None else [IVA19],
        account_id=41,
    )


def build(kind: str, lines: list[ResolvedLine]) -> list[dict]:  # type: ignore[type-arg]
    totals = compute_totals(lines, decimals=2)
    return build_invoice_lines(
        kind=kind,
        resolved_lines=lines,
        totals=totals,
        taxes_by_code=TAX_ROWS,
        counterpart_account_id=11,
        partner_id=7,
        fallback_account_id=None,
        error_prefix="SALE",
        decimals=2,
    )


class TestBalanceInvariant:
    @settings(max_examples=200, deadline=None)
    @given(
        kind=st.sampled_from(["customer", "vendor"]),
        lines=st.lists(
            st.tuples(prices, quantities, discounts),
            min_size=1,
            max_size=6,
        ),
        with_withholding=st.booleans(),
    )
    def test_any_invoice_balances(
        self,
        kind: str,
        lines: list[tuple[Decimal, Decimal, Decimal]],
        with_withholding: bool,
    ) -> None:
        """Sea cual sea la combinación de líneas, el asiento pasa validate_lines."""
        taxes = [IVA19, RET10] if with_withholding else [IVA19]
        resolved = [line(p, q, d, taxes) for p, q, d in lines]
        try:
            entries = build(kind, resolved)
        except AccountingError as exc:
            # Un documento que redondea a cero no se asienta: error estable.
            assert exc.code == "SALE_ZERO_TOTAL"
            return
        total_debit = validate_lines(entries)  # levanta si no cuadra
        assert total_debit > 0

    def test_customer_invoice_shape(self) -> None:
        entries = build("customer", [line(Decimal("100000"))])
        by_account = {entry["account_id"]: entry for entry in entries}
        assert by_account[11]["debit"] == Decimal("119000.00")  # por cobrar
        assert by_account[41]["credit"] == Decimal("100000.00")  # ingreso
        assert by_account[61]["credit"] == Decimal("19000.00")  # IVA débito
        assert by_account[11]["partner_id"] == 7

    def test_vendor_bill_is_the_mirror(self) -> None:
        entries = build("vendor", [line(Decimal("100000"))])
        by_account = {entry["account_id"]: entry for entry in entries}
        assert by_account[11]["credit"] == Decimal("119000.00")  # por pagar
        assert by_account[41]["debit"] == Decimal("100000.00")  # gasto
        assert by_account[61]["debit"] == Decimal("19000.00")  # IVA crédito

    def test_withholding_reduces_the_receivable(self) -> None:
        entries = build("customer", [line(Decimal("100000"), taxes=[IVA19, RET10])])
        by_account = {entry["account_id"]: entry for entry in entries}
        # Retención 10% sobre la base: 10.000; por cobrar = 119.000 - 10.000
        assert by_account[11]["debit"] == Decimal("109000.00")
        assert by_account[62]["debit"] == Decimal("10000.00")
        validate_lines(entries)

    def test_line_without_account_and_no_fallback_fails(self) -> None:
        no_account = ResolvedLine(
            name="sin cuenta",
            quantity=Decimal("1"),
            price_unit=Decimal("100"),
            discount_percent=Decimal("0"),
            taxes=[],
            account_id=None,
        )
        with pytest.raises(AccountingError) as excinfo:
            build("customer", [no_account])
        assert excinfo.value.code == "SALE_NO_ACCOUNT"

    def test_tax_without_account_fails(self) -> None:
        naked = Tax(code="NAKED", name="Sin cuenta", amount=Decimal("5"))
        rows = {"NAKED": ({"account_id": None, "code": "NAKED"}, naked)}
        resolved = [line(Decimal("100"), taxes=[naked])]
        totals = compute_totals(resolved, decimals=2)
        with pytest.raises(AccountingError) as excinfo:
            build_invoice_lines(
                kind="customer",
                resolved_lines=resolved,
                totals=totals,
                taxes_by_code=rows,
                counterpart_account_id=11,
                partner_id=7,
                fallback_account_id=None,
                error_prefix="SALE",
                decimals=2,
            )
        assert excinfo.value.code == "ACCOUNT_TAX_NO_ACCOUNT"
