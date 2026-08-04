"""Notas de crédito: la corrección comercial, contable y electrónica en un flujo."""

import xml.etree.ElementTree as ET
from decimal import Decimal
from typing import Any

import pytest
from modules.einvoicing.tests.test_sii import make_caf, make_key
from modules.sale.services import SaleError, SaleService
from ordo_core.actions import dispatch
from ordo_core.recordset import RecordSet

pytestmark = pytest.mark.integration

ACCEPTED = b"<RECEPCIONDTE><STATUS>0</STATUS><TRACKID>1</TRACKID></RECEPCIONDTE>"


class StubSigner:
    def sign(self, xml: bytes, reference: str) -> bytes:
        return xml


class StubTransport:
    async def send(self, payload: bytes) -> bytes:
        return ACCEPTED


async def invoiced_order(shop: dict[str, Any]) -> int:
    service = SaleService(shop["env"])
    order_id = await service.create_order(
        partner_id=shop["customer_id"],
        date_order="2026-08-04",
        currency_id=shop["currency_id"],
        journal_id=shop["sale_journal"],
        company_id=shop["company_id"],
        lines=[
            {
                "name": "Licencia anual",
                "quantity": "1",
                "price_unit": Decimal("100000"),
                "tax_codes": "IVA19",
            }
        ],
    )
    await service.action_confirm(order_id)
    await service.action_invoice(order_id)
    return order_id


async def load_folios(shop: dict[str, Any], doc_type: str) -> None:
    await RecordSet(shop["env"], "edi.folio.range").create(
        [
            {
                "country_code": "cl",
                "document_type_code": doc_type,
                "range_from": 1,
                "range_to": 100,
                "next_number": 1,
                "authorization_code": make_caf(make_key(), doc_type=doc_type),
                "company_id": shop["company_id"],
            }
        ]
    )


class TestCommercialCreditNote:
    async def test_credit_note_reverses_the_invoice_completely(self, shop: dict[str, Any]) -> None:
        """El neto por cuenta tras factura + NC es exactamente cero."""
        order_id = await invoiced_order(shop)
        result = await dispatch(
            shop["env"],
            "sale.order",
            "action_credit_note",
            order_id,
            {"reason": "Anulación de la venta"},
        )
        assert result["state"] == "credited"

        [order] = await RecordSet(shop["env"], "sale.order").read(
            [order_id], fields=["state", "invoice_move_id", "credit_note_move_id"]
        )
        assert order["state"] == "credited"

        lines = RecordSet(shop["env"], "account.move.line")
        totals: dict[int, Decimal] = {}
        for move_id in (order["invoice_move_id"], order["credit_note_move_id"]):
            page = await lines.search(
                [("move_id", "=", move_id)], fields=["account_id", "debit", "credit"]
            )
            for row in page["rows"]:
                totals[row["account_id"]] = (
                    totals.get(row["account_id"], Decimal("0")) + row["debit"] - row["credit"]
                )
        assert all(value == Decimal("0") for value in totals.values())

    async def test_reason_is_mandatory(self, shop: dict[str, Any]) -> None:
        order_id = await invoiced_order(shop)
        with pytest.raises(SaleError) as excinfo:
            await SaleService(shop["env"]).action_credit_note(order_id, reason="  ")
        assert excinfo.value.code == "SALE_CREDIT_REASON_REQUIRED"

    async def test_crediting_twice_is_impossible(self, shop: dict[str, Any]) -> None:
        order_id = await invoiced_order(shop)
        await SaleService(shop["env"]).action_credit_note(order_id, reason="Anulación")
        with pytest.raises(SaleError) as excinfo:
            await SaleService(shop["env"]).action_credit_note(order_id, reason="Otra vez")
        assert excinfo.value.code == "SALE_INVALID_TRANSITION"

    async def test_only_invoiced_orders_get_credit_notes(self, shop: dict[str, Any]) -> None:
        service = SaleService(shop["env"])
        order_id = await service.create_order(
            partner_id=shop["customer_id"],
            date_order="2026-08-04",
            currency_id=shop["currency_id"],
            journal_id=shop["sale_journal"],
            company_id=shop["company_id"],
            lines=[{"name": "x", "price_unit": Decimal("100")}],
        )
        with pytest.raises(SaleError) as excinfo:
            await service.action_credit_note(order_id, reason="No corresponde")
        assert excinfo.value.code == "SALE_INVALID_TRANSITION"


class TestElectronicCreditNote:
    async def test_nc_references_the_original_dte(self, shop: dict[str, Any]) -> None:
        """La NC 61 sale con Referencia al DTE 33 original enviado."""
        from modules.einvoicing.runtime import default_registry
        from modules.einvoicing.services import EinvoicingService

        order_id = await invoiced_order(shop)
        await load_folios(shop, "33")
        await load_folios(shop, "61")

        issued = await dispatch(
            shop["env"],
            "sale.order",
            "action_einvoice",
            order_id,
            {"document_type_code": "33"},
        )
        edi_service = EinvoicingService(shop["env"], default_registry())
        await edi_service.action_sign(issued["document_id"], StubSigner())
        await edi_service.action_send(issued["document_id"], StubTransport())

        result = await dispatch(
            shop["env"],
            "sale.order",
            "action_einvoice_credit_note",
            order_id,
            {"original_document_id": issued["document_id"], "reason": "Anula factura"},
        )
        assert result["document_type_code"] == "61"
        assert result["references"] == f"33/{issued['number']}"

        [document] = await RecordSet(shop["env"], "edi.document").read(
            [result["document_id"]], fields=["xml_payload", "document_type_code"]
        )
        doc = ET.fromstring(document["xml_payload"]).find("Documento")
        assert doc is not None
        assert doc.findtext("Encabezado/IdDoc/TipoDTE") == "61"
        assert doc.findtext("Referencia/TpoDocRef") == "33"
        assert doc.findtext("Referencia/FolioRef") == str(issued["number"])
        assert doc.findtext("Referencia/RazonRef") == "Anula factura"

    async def test_draft_original_cannot_be_credited(self, shop: dict[str, Any]) -> None:
        from modules.einvoicing.statemachine import EdiError

        order_id = await invoiced_order(shop)
        await load_folios(shop, "33")
        issued = await dispatch(
            shop["env"],
            "sale.order",
            "action_einvoice",
            order_id,
            {"document_type_code": "33"},
        )
        # generado pero nunca enviado: se anula, no se corrige
        with pytest.raises(EdiError) as excinfo:
            await dispatch(
                shop["env"],
                "sale.order",
                "action_einvoice_credit_note",
                order_id,
                {"original_document_id": issued["document_id"], "reason": "x"},
            )
        assert excinfo.value.code == "EDI_CREDIT_ORIGINAL_NOT_ISSUED"
