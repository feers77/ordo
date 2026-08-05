"""La boleta del ticket: folio, receptor anónimo y referencia de la devolución."""

from typing import Any

import pytest
from modules.einvoicing.statemachine import EdiError
from modules.einvoicing.tests.test_sii import make_caf, make_key
from modules.pos.services import PosError
from ordo_core.actions import dispatch
from ordo_core.recordset import RecordSet

from tests.integration.pos.conftest import stock_in
from tests.integration.pos.test_order import pay, ticket
from tests.integration.pos.test_session import opened_session

pytestmark = pytest.mark.integration


async def load_folios(shop: dict[str, Any], *document_types: str) -> None:
    """Los rangos autorizados con su CAF. La boleta y su nota de crédito."""
    for document_type in document_types:
        await RecordSet(shop["env"], "edi.folio.range").create(
            [
                {
                    "country_code": "cl",
                    "document_type_code": document_type,
                    "range_from": 1,
                    "range_to": 100,
                    "next_number": 1,
                    "authorization_code": make_caf(
                        make_key(), doc_type=document_type, range_to=100
                    ),
                    "company_id": shop["company_id"],
                }
            ]
        )


async def sold_ticket(shop: dict[str, Any], session_id: int, *, price: str = "23800") -> int:
    order_id = await ticket(shop, session_id, price=price)
    await pay(shop, order_id, method="method_cash", amount=price)
    await dispatch(shop["env"], "pos.order", "action_validate", order_id, {})
    return order_id


class TestBoleta:
    async def test_an_anonymous_ticket_still_gets_its_boleta(self, shop: dict[str, Any]) -> None:
        """En retail casi todos los tickets son anónimos y la boleta se emite
        igual, con el contacto genérico de la caja."""
        await load_folios(shop, "39")
        await stock_in(shop, "50", "8000")
        session_id = await opened_session(shop, "50000")
        order_id = await sold_ticket(shop, session_id)

        result = await dispatch(shop["env"], "pos.order", "action_einvoice", order_id, {})
        assert result["document_type_code"] == "39"  # el tipo lo da la caja
        assert result["number"] == 1
        assert result["state"] == "generated"

        [document] = await RecordSet(shop["env"], "edi.document").read(
            [result["document_id"]], fields=["state", "xml_payload", "payload_encoding"]
        )
        assert document["state"] == "generated"
        assert document["payload_encoding"] == "iso-8859-1"
        xml = document["xml_payload"]
        assert "<TipoDTE>39</TipoDTE>" in xml
        assert "<IndServicio>3</IndServicio>" in xml  # el SII lo exige en boletas
        assert "<RUTRecep>66666666-6</RUTRecep>" in xml

        [order] = await RecordSet(shop["env"], "pos.order").read(
            [order_id], fields=["edi_document_id"]
        )
        assert order["edi_document_id"] == result["document_id"]

    async def test_an_identified_customer_goes_on_the_boleta(self, shop: dict[str, Any]) -> None:
        await load_folios(shop, "39")
        await stock_in(shop, "50", "8000")
        session_id = await opened_session(shop, "50000")
        order_id = await ticket(shop, session_id, price="23800")
        await RecordSet(shop["env"], "pos.order").write(
            [order_id], {"partner_id": shop["customer_id"]}
        )
        await pay(shop, order_id, method="method_cash", amount="23800")
        await dispatch(shop["env"], "pos.order", "action_validate", order_id, {})

        result = await dispatch(shop["env"], "pos.order", "action_einvoice", order_id, {})
        [document] = await RecordSet(shop["env"], "edi.document").read(
            [result["document_id"]], fields=["xml_payload"]
        )
        assert "<RUTRecep>66666666-6</RUTRecep>" not in document["xml_payload"]

    async def test_a_ticket_carries_one_boleta(self, shop: dict[str, Any]) -> None:
        await load_folios(shop, "39")
        await stock_in(shop, "50", "8000")
        session_id = await opened_session(shop, "50000")
        order_id = await sold_ticket(shop, session_id)
        await dispatch(shop["env"], "pos.order", "action_einvoice", order_id, {})
        with pytest.raises(PosError) as excinfo:
            await dispatch(shop["env"], "pos.order", "action_einvoice", order_id, {})
        assert excinfo.value.code == "POS_ALREADY_INVOICED"

    async def test_an_uncharged_ticket_emits_nothing(self, shop: dict[str, Any]) -> None:
        await load_folios(shop, "39")
        await stock_in(shop, "50", "8000")
        session_id = await opened_session(shop, "50000")
        order_id = await ticket(shop, session_id, price="23800")
        with pytest.raises(EdiError) as excinfo:
            await dispatch(shop["env"], "pos.order", "action_einvoice", order_id, {})
        assert excinfo.value.code == "EDI_SOURCE_NOT_READY"


class TestRefundDocument:
    async def test_a_refund_emits_a_credit_note_not_another_boleta(
        self, shop: dict[str, Any]
    ) -> None:
        """Emitir un 39 por una devolución sumaría venta en vez de restarla."""
        await load_folios(shop, "39", "61")
        await stock_in(shop, "50", "8000")
        session_id = await opened_session(shop, "50000")
        order_id = await sold_ticket(shop, session_id)
        await dispatch(shop["env"], "pos.order", "action_einvoice", order_id, {})

        refund = await dispatch(
            shop["env"], "pos.order", "action_refund", order_id, {"reason": "talla"}
        )
        result = await dispatch(
            shop["env"], "pos.order", "action_einvoice", refund["refund_id"], {}
        )
        assert result["document_type_code"] == "61"

        [document] = await RecordSet(shop["env"], "edi.document").read(
            [result["document_id"]], fields=["xml_payload"]
        )
        xml = document["xml_payload"]
        assert "<TipoDTE>61</TipoDTE>" in xml
        # sin referencia, la autoridad no sabe qué corrige y rechaza
        assert "<TpoDocRef>39</TpoDocRef>" in xml
        assert "<FolioRef>1</FolioRef>" in xml
        assert "Devolución" in xml

    async def test_a_refund_of_an_unbilled_ticket_says_so(self, shop: dict[str, Any]) -> None:
        """Sin documento original no hay a qué referirse; se avisa en vez de
        emitir una referencia inventada."""
        await load_folios(shop, "39", "61")
        await stock_in(shop, "50", "8000")
        session_id = await opened_session(shop, "50000")
        order_id = await sold_ticket(shop, session_id)
        refund = await dispatch(
            shop["env"], "pos.order", "action_refund", order_id, {"reason": "talla"}
        )
        with pytest.raises(EdiError) as excinfo:
            await dispatch(shop["env"], "pos.order", "action_einvoice", refund["refund_id"], {})
        assert excinfo.value.code == "EDI_REFERENCE_MISSING"


class TestSimulation:
    async def test_dry_run_does_not_burn_a_folio(self, shop: dict[str, Any]) -> None:
        """El folio es un recurso legal y escaso: simular no puede gastarlo."""
        await load_folios(shop, "39")
        await stock_in(shop, "50", "8000")
        session_id = await opened_session(shop, "50000")
        order_id = await sold_ticket(shop, session_id)

        simulated = await dispatch(
            shop["env"], "pos.order", "action_einvoice", order_id, {}, dry_run=True
        )
        assert simulated["would_return"]["number"] == 1

        real = await dispatch(shop["env"], "pos.order", "action_einvoice", order_id, {})
        assert real["number"] == 1  # el folio no se gastó en la simulación
