"""De la orden al asiento contabilizado, sin manos: ventas y compras."""

from decimal import Decimal
from typing import Any

import pytest
from modules.account.services import AccountingError
from modules.purchase.services import PurchaseError, PurchaseService
from modules.sale.services import SaleError, SaleService
from ordo_core.recordset import RecordSet

pytestmark = pytest.mark.integration


async def make_sale(shop: dict[str, Any], **overrides: Any) -> int:
    service = SaleService(shop["env"])
    kwargs: dict[str, Any] = {
        "partner_id": shop["customer_id"],
        "date_order": "2026-08-04",
        "currency_id": shop["currency_id"],
        "journal_id": shop["sale_journal"],
        "company_id": shop["company_id"],
        "lines": [
            {
                "name": "Licencia anual",
                "quantity": "1",
                "price_unit": Decimal("100000"),
                "tax_codes": "IVA19",
            }
        ],
    }
    kwargs.update(overrides)
    return await service.create_order(**kwargs)


async def move_lines_of(shop: dict[str, Any], move_id: int) -> dict[int, dict[str, Any]]:
    lines = RecordSet(shop["env"], "account.move.line")
    result = await lines.search(
        [("move_id", "=", move_id)],
        fields=["account_id", "debit", "credit", "partner_id"],
        limit=100,
    )
    return {row["account_id"]: row for row in result["rows"]}


class TestSale:
    async def test_confirm_fixes_totals_and_number(self, shop: dict[str, Any]) -> None:
        service = SaleService(shop["env"])
        order_id = await make_sale(shop)
        number = await service.action_confirm(order_id)
        assert number == "SO/00001"

        [order] = await service.orders.read(
            [order_id], fields=["state", "amount_untaxed", "amount_tax", "amount_total"]
        )
        assert order["state"] == "confirmed"
        assert order["amount_untaxed"] == Decimal("100000")
        assert order["amount_tax"] == Decimal("19000")
        assert order["amount_total"] == Decimal("119000")

    async def test_invoice_posts_a_balanced_move(self, shop: dict[str, Any]) -> None:
        """El asiento se genera y contabiliza solo: cliente al debe, venta e IVA al haber."""
        service = SaleService(shop["env"])
        order_id = await make_sale(shop)
        await service.action_confirm(order_id)
        move_id = await service.action_invoice(order_id)

        moves = RecordSet(shop["env"], "account.move")
        [move] = await moves.read([move_id], fields=["state", "name", "partner_id"])
        assert move["state"] == "posted"
        assert move["name"] == "VTA/2026/00001"
        assert move["partner_id"] == shop["customer_id"]

        by_account = await move_lines_of(shop, move_id)
        assert by_account[shop["clientes"]]["debit"] == Decimal("119000")
        assert by_account[shop["ventas"]]["credit"] == Decimal("100000")
        assert by_account[shop["iva_debito"]]["credit"] == Decimal("19000")
        assert by_account[shop["clientes"]]["partner_id"] == shop["customer_id"]

        [order] = await service.orders.read([order_id], fields=["state", "invoice_move_id"])
        assert order["state"] == "invoiced"
        assert order["invoice_move_id"] == move_id

    async def test_withholding_reduces_the_receivable(self, shop: dict[str, Any]) -> None:
        service = SaleService(shop["env"])
        order_id = await make_sale(
            shop,
            lines=[
                {
                    "name": "Servicio profesional",
                    "quantity": "1",
                    "price_unit": Decimal("100000"),
                    "tax_codes": "IVA19,RET10",
                }
            ],
        )
        await service.action_confirm(order_id)
        move_id = await service.action_invoice(order_id)

        by_account = await move_lines_of(shop, move_id)
        assert by_account[shop["clientes"]]["debit"] == Decimal("109000")
        assert by_account[shop["retenciones"]]["debit"] == Decimal("10000")
        assert by_account[shop["iva_debito"]]["credit"] == Decimal("19000")

    async def test_unknown_tax_code_is_a_stable_error(self, shop: dict[str, Any]) -> None:
        service = SaleService(shop["env"])
        order_id = await make_sale(
            shop,
            lines=[
                {
                    "name": "x",
                    "price_unit": Decimal("100"),
                    "tax_codes": "NO_EXISTE",
                }
            ],
        )
        with pytest.raises(AccountingError) as excinfo:
            await service.action_confirm(order_id)
        assert excinfo.value.code == "SALE_TAX_UNKNOWN"

    async def test_purchase_tax_cannot_be_used_in_sales(self, shop: dict[str, Any]) -> None:
        service = SaleService(shop["env"])
        order_id = await make_sale(
            shop,
            lines=[{"name": "x", "price_unit": Decimal("100"), "tax_codes": "IVA19C"}],
        )
        with pytest.raises(AccountingError) as excinfo:
            await service.action_confirm(order_id)
        assert excinfo.value.code == "SALE_TAX_UNKNOWN"

    async def test_invoiced_order_cannot_be_cancelled(self, shop: dict[str, Any]) -> None:
        service = SaleService(shop["env"])
        order_id = await make_sale(shop)
        await service.action_confirm(order_id)
        await service.action_invoice(order_id)
        with pytest.raises(SaleError) as excinfo:
            await service.action_cancel(order_id)
        assert excinfo.value.code == "SALE_INVALID_TRANSITION"

    async def test_invoicing_twice_is_impossible(self, shop: dict[str, Any]) -> None:
        service = SaleService(shop["env"])
        order_id = await make_sale(shop)
        await service.action_confirm(order_id)
        await service.action_invoice(order_id)
        with pytest.raises(SaleError) as excinfo:
            await service.action_invoice(order_id)
        assert excinfo.value.code == "SALE_INVALID_TRANSITION"


class TestPurchase:
    async def make_purchase(self, shop: dict[str, Any]) -> int:
        service = PurchaseService(shop["env"])
        return await service.create_order(
            partner_id=shop["vendor_id"],
            date_order="2026-08-04",
            currency_id=shop["currency_id"],
            journal_id=shop["purchase_journal"],
            company_id=shop["company_id"],
            lines=[
                {
                    "name": "Hosting agosto",
                    "quantity": "1",
                    "price_unit": Decimal("50000"),
                    "tax_codes": "IVA19C",
                }
            ],
        )

    async def test_bill_posts_the_mirror_move(self, shop: dict[str, Any]) -> None:
        service = PurchaseService(shop["env"])
        order_id = await self.make_purchase(shop)
        number = await service.action_confirm(order_id)
        assert number == "PO/00001"

        move_id = await service.action_bill(order_id, vendor_ref="F-4581")
        by_account = await move_lines_of(shop, move_id)
        assert by_account[shop["proveedores"]]["credit"] == Decimal("59500")
        assert by_account[shop["gastos"]]["debit"] == Decimal("50000")
        assert by_account[shop["iva_credito"]]["debit"] == Decimal("9500")

        [order] = await service.orders.read(
            [order_id], fields=["state", "bill_move_id", "vendor_ref"]
        )
        assert order["state"] == "billed"
        assert order["bill_move_id"] == move_id
        assert order["vendor_ref"] == "F-4581"

    async def test_bill_requires_the_vendor_reference(self, shop: dict[str, Any]) -> None:
        service = PurchaseService(shop["env"])
        order_id = await self.make_purchase(shop)
        await service.action_confirm(order_id)
        with pytest.raises(PurchaseError) as excinfo:
            await service.action_bill(order_id, vendor_ref="   ")
        assert excinfo.value.code == "PURCHASE_VENDOR_REF_REQUIRED"

    async def test_draft_order_can_be_cancelled(self, shop: dict[str, Any]) -> None:
        service = PurchaseService(shop["env"])
        order_id = await self.make_purchase(shop)
        await service.action_cancel(order_id)
        [order] = await service.orders.read([order_id], fields=["state"])
        assert order["state"] == "cancelled"
