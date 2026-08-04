"""Ciclo de vida completo de un DTE contra base real: folio, firma, envío, acuse."""

from typing import Any

import pytest
from localizations.cl.einvoicing.adapter import SiiAdapter
from modules.einvoicing.contracts import AdapterRegistry
from modules.einvoicing.services import EinvoicingService, FolioService
from modules.einvoicing.statemachine import EdiError
from modules.einvoicing.tests.test_sii import make_caf, make_invoice, make_key
from ordo_core.recordset import RecordSet

pytestmark = pytest.mark.integration

ACCEPTED = b"<RECEPCIONDTE><STATUS>0</STATUS><TRACKID>424242</TRACKID></RECEPCIONDTE>"
VERDICT_OK = b"<RESPUESTA><ESTADO>EPR</ESTADO><GLOSA>Envio Procesado</GLOSA></RESPUESTA>"
VERDICT_BAD = b"<RESPUESTA><ESTADO>RCT</ESTADO><GLOSA>Error de schema</GLOSA></RESPUESTA>"


class StubSigner:
    def sign(self, xml: bytes, reference: str) -> bytes:
        return xml + b"<!--firmado-->"


class StubTransport:
    def __init__(self, response: bytes) -> None:
        self.response = response
        self.sent: list[bytes] = []

    async def send(self, payload: bytes) -> bytes:
        self.sent.append(payload)
        return self.response


def make_service(edi: dict[str, Any]) -> EinvoicingService:
    registry = AdapterRegistry()
    registry.register(SiiAdapter())
    return EinvoicingService(edi["env"], registry)


async def load_folios(edi: dict[str, Any], *, range_to: int = 100) -> int:
    ranges = RecordSet(edi["env"], "edi.folio.range")
    [range_id] = await ranges.create(
        [
            {
                "country_code": "cl",
                "document_type_code": "33",
                "range_from": 1,
                "range_to": range_to,
                "next_number": 1,
                "authorization_code": make_caf(make_key(), range_to=range_to),
                "company_id": edi["company_id"],
            }
        ]
    )
    return range_id


async def make_document(edi: dict[str, Any], service: EinvoicingService) -> int:
    return await service.create_document(
        country_code="cl",
        document_type_code="33",
        company_id=edi["company_id"],
    )


class TestLifecycle:
    async def test_full_cycle_ends_accepted(self, edi: dict[str, Any]) -> None:
        await load_folios(edi)
        service = make_service(edi)
        document_id = await make_document(edi, service)

        folio = await service.action_generate(document_id, make_invoice())
        assert folio == 1

        await service.action_sign(document_id, StubSigner())
        track_id = await service.action_send(document_id, StubTransport(ACCEPTED))
        assert track_id == "424242"

        state = await service.action_check(document_id, StubTransport(VERDICT_OK))
        assert state == "accepted"

        documents = RecordSet(edi["env"], "edi.document")
        [doc] = await documents.read(
            [document_id], fields=["state", "number", "track_id", "attempts"]
        )
        assert doc["state"] == "accepted"
        assert doc["number"] == 1
        assert doc["attempts"] == 1

    async def test_rejection_allows_regenerating_with_a_new_folio(
        self, edi: dict[str, Any]
    ) -> None:
        await load_folios(edi)
        service = make_service(edi)
        document_id = await make_document(edi, service)

        await service.action_generate(document_id, make_invoice())
        await service.action_sign(document_id, StubSigner())
        await service.action_send(document_id, StubTransport(ACCEPTED))
        state = await service.action_check(document_id, StubTransport(VERDICT_BAD))
        assert state == "rejected"

        folio = await service.action_generate(document_id, make_invoice())
        assert folio == 2  # el folio rechazado quedó quemado

    async def test_contingency_resends_without_burning_the_folio(self, edi: dict[str, Any]) -> None:
        await load_folios(edi)
        service = make_service(edi)
        document_id = await make_document(edi, service)

        await service.action_generate(document_id, make_invoice())
        await service.action_sign(document_id, StubSigner())
        await service.action_contingency(document_id)

        track_id = await service.action_send(document_id, StubTransport(ACCEPTED))
        assert track_id == "424242"
        documents = RecordSet(edi["env"], "edi.document")
        [doc] = await documents.read([document_id], fields=["number", "state"])
        assert doc["number"] == 1
        assert doc["state"] == "sent"

    async def test_cancelling_an_accepted_dte_is_refused_in_chile(
        self, edi: dict[str, Any]
    ) -> None:
        await load_folios(edi)
        service = make_service(edi)
        document_id = await make_document(edi, service)
        await service.action_generate(document_id, make_invoice())
        await service.action_sign(document_id, StubSigner())
        await service.action_send(document_id, StubTransport(ACCEPTED))
        await service.action_check(document_id, StubTransport(VERDICT_OK))

        with pytest.raises(EdiError) as excinfo:
            await service.action_cancel(document_id)
        assert excinfo.value.code == "EDI_CANCEL_UNSUPPORTED"

    async def test_skipping_the_signature_is_impossible(self, edi: dict[str, Any]) -> None:
        await load_folios(edi)
        service = make_service(edi)
        document_id = await make_document(edi, service)
        await service.action_generate(document_id, make_invoice())
        with pytest.raises(EdiError) as excinfo:
            await service.action_send(document_id, StubTransport(ACCEPTED))
        assert excinfo.value.code == "EDI_INVALID_TRANSITION"


class TestFolios:
    async def test_exhausting_the_range_is_a_stable_error(self, edi: dict[str, Any]) -> None:
        await load_folios(edi, range_to=1)
        service = make_service(edi)
        first = await make_document(edi, service)
        await service.action_generate(first, make_invoice())

        second = await make_document(edi, service)
        with pytest.raises(EdiError) as excinfo:
            await service.action_generate(second, make_invoice())
        assert excinfo.value.code == "EDI_FOLIO_EXHAUSTED"

    async def test_expired_range_is_a_stable_error(self, edi: dict[str, Any]) -> None:
        ranges = RecordSet(edi["env"], "edi.folio.range")
        await ranges.create(
            [
                {
                    "country_code": "cl",
                    "document_type_code": "33",
                    "range_from": 1,
                    "range_to": 100,
                    "next_number": 1,
                    "authorization_code": make_caf(make_key()),
                    "valid_until": "2020-01-01",
                    "company_id": edi["company_id"],
                }
            ]
        )
        service = FolioService(edi["env"])
        with pytest.raises(EdiError) as excinfo:
            await service.assign(
                country_code="cl",
                document_type_code="33",
                company_id=edi["company_id"],
            )
        assert excinfo.value.code == "EDI_FOLIO_EXPIRED"
