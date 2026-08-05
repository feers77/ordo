"""Adaptador SII: CAF, timbre firmado de verdad y estructura del DTE."""

import base64
import xml.etree.ElementTree as ET
from datetime import UTC, date, datetime
from decimal import Decimal

import pytest
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from localizations.cl.einvoicing.adapter import SiiAdapter
from localizations.cl.einvoicing.caf import CafError, parse_caf
from localizations.cl.einvoicing.dte import DteError, build_document
from localizations.cl.einvoicing.envelope import EnvelopeData, build_envelope
from localizations.cl.einvoicing.responses import (
    parse_status_response,
    parse_upload_response,
)
from localizations.cl.einvoicing.ted import TedError, build_ted
from ordo_core.taxid import rut_check_digit

from modules.account.taxes import Tax, compute_document
from modules.einvoicing.contracts import (
    FolioAssignment,
    InvoiceData,
    InvoiceLine,
    Party,
)

NOW = datetime(2026, 8, 4, 12, 0, 0, tzinfo=UTC)
IVA19 = Tax(code="IVA19", name="IVA 19%", amount=Decimal("19"))


def rut(number: int) -> str:
    return f"{number}-{rut_check_digit(number)}"


ISSUER_RUT = rut(76543210)
RECEIVER_RUT = rut(12345678)


def make_key() -> rsa.RSAPrivateKey:
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


def make_caf(
    key: rsa.RSAPrivateKey,
    *,
    doc_type: str = "33",
    range_from: int = 1,
    range_to: int = 100,
) -> str:
    pem = key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.TraditionalOpenSSL,
        serialization.NoEncryption(),
    ).decode()
    return (
        '<AUTORIZACION><CAF version="1.0"><DA>'
        f"<RE>{ISSUER_RUT}</RE><RS>ACME SPA</RS><TD>{doc_type}</TD>"
        f"<RNG><D>{range_from}</D><H>{range_to}</H></RNG>"
        "<FA>2026-01-01</FA><RSAPK><M>bW9k</M><E>QUFC</E></RSAPK><IDK>100</IDK>"
        '</DA><FRMA algoritmo="SHA1withRSA">ZmlybWE=</FRMA></CAF>'
        f"<RSASK>{pem}</RSASK><RSAPUBK></RSAPUBK></AUTORIZACION>"
    )


def make_invoice(
    *,
    doc_type: str = "33",
    reference: str = "",
    reason: str = "",
) -> InvoiceData:
    lines = [
        {"price_unit": Decimal("100000"), "quantity": Decimal("1"), "taxes": [IVA19]},
    ]
    return InvoiceData(
        document_type_code=doc_type,
        issue_date=date(2026, 8, 4),
        issuer=Party(tax_id=ISSUER_RUT, name="ACME SpA", activity="Software"),
        receiver=Party(tax_id=RECEIVER_RUT, name="Cliente Ltda"),
        lines=(
            InvoiceLine(
                description="Licencia anual",
                quantity=Decimal("1"),
                price_unit=Decimal("100000"),
            ),
        ),
        taxes=compute_document(lines, decimals=0),
        currency="CLP",
        reference_document=reference,
        reference_reason=reason,
    )


class TestCaf:
    def test_parses_the_authorization(self) -> None:
        caf = parse_caf(make_caf(make_key()))
        assert caf.issuer_rut == ISSUER_RUT
        assert caf.document_type == "33"
        assert caf.covers(1) and caf.covers(100)
        assert not caf.covers(101)
        assert "<CAF" in caf.caf_xml

    def test_caf_without_private_key_is_rejected(self) -> None:
        raw = make_caf(make_key()).replace("<RSASK>", "<X>").replace("</RSASK>", "</X>")
        with pytest.raises(CafError) as excinfo:
            parse_caf(raw)
        assert excinfo.value.code == "CL_CAF_NO_KEY"

    def test_garbage_is_not_a_caf(self) -> None:
        with pytest.raises(CafError):
            parse_caf("esto no es xml")


class TestTed:
    def test_signature_verifies_with_the_caf_key(self) -> None:
        """El FRMT debe validar RSA-SHA1 contra la clave pública del CAF."""
        key = make_key()
        caf = parse_caf(make_caf(key))
        ted = build_ted(make_invoice(), 42, caf, now=NOW)

        root = ET.fromstring(ted)
        frmt = root.find("FRMT")
        assert frmt is not None and frmt.text
        dd = ted[ted.index("<DD>") : ted.index("</DD>") + len("</DD>")]
        key.public_key().verify(
            base64.b64decode(frmt.text),
            dd.encode("iso-8859-1"),
            padding.PKCS1v15(),
            hashes.SHA1(),  # el formato TED del SII exige SHA1withRSA
        )

    def test_folio_outside_the_caf_range_is_rejected(self) -> None:
        caf = parse_caf(make_caf(make_key(), range_to=10))
        with pytest.raises(TedError) as excinfo:
            build_ted(make_invoice(), 11, caf, now=NOW)
        assert excinfo.value.code == "CL_FOLIO_OUT_OF_CAF"

    def test_caf_of_another_document_type_is_rejected(self) -> None:
        caf = parse_caf(make_caf(make_key(), doc_type="39"))
        with pytest.raises(TedError) as excinfo:
            build_ted(make_invoice(), 5, caf, now=NOW)
        assert excinfo.value.code == "CL_CAF_WRONG_TYPE"

    def test_total_in_ted_matches_the_tax_engine(self) -> None:
        caf = parse_caf(make_caf(make_key()))
        ted = build_ted(make_invoice(), 7, caf, now=NOW)
        mnt = ET.fromstring(ted).findtext("DD/MNT")
        assert mnt == "119000"


class TestDte:
    def test_renders_a_complete_type_33(self) -> None:
        adapter = SiiAdapter(now=NOW)
        folio = FolioAssignment(number=42, authorization_code=make_caf(make_key()))
        xml = adapter.render(make_invoice(), folio)

        root = ET.fromstring(xml.decode("iso-8859-1"))
        doc = root.find("Documento")
        assert doc is not None
        assert doc.attrib["ID"] == "F42T33"
        assert doc.findtext("Encabezado/IdDoc/TipoDTE") == "33"
        assert doc.findtext("Encabezado/IdDoc/Folio") == "42"
        assert doc.findtext("Encabezado/Emisor/RUTEmisor") == ISSUER_RUT
        assert doc.findtext("Encabezado/Totales/MntNeto") == "100000"
        assert doc.findtext("Encabezado/Totales/IVA") == "19000"
        assert doc.findtext("Encabezado/Totales/MntTotal") == "119000"
        assert doc.findtext("Detalle/NmbItem") == "Licencia anual"
        assert doc.find("TED/DD") is not None

    def test_invalid_rut_never_renders(self) -> None:
        adapter = SiiAdapter(now=NOW)
        invoice = make_invoice()
        bad = InvoiceData(
            document_type_code=invoice.document_type_code,
            issue_date=invoice.issue_date,
            issuer=Party(tax_id="76543210-0", name="ACME"),
            receiver=invoice.receiver,
            lines=invoice.lines,
            taxes=invoice.taxes,
            currency="CLP",
        )
        folio = FolioAssignment(number=1, authorization_code=make_caf(make_key()))
        with pytest.raises(Exception) as excinfo:
            adapter.render(bad, folio)
        assert getattr(excinfo.value, "code", "") == "TAXID_INVALID_CHECK_DIGIT"

    def test_credit_note_requires_a_reference(self) -> None:
        adapter = SiiAdapter(now=NOW)
        key = make_key()
        folio = FolioAssignment(number=3, authorization_code=make_caf(key, doc_type="61"))
        with pytest.raises(DteError) as excinfo:
            adapter.render(make_invoice(doc_type="61"), folio)
        assert excinfo.value.code == "CL_DTE_REFERENCE_REQUIRED"

        xml = adapter.render(
            make_invoice(doc_type="61", reference="33/1042", reason="Anula factura"),
            folio,
        )
        doc = ET.fromstring(xml.decode("iso-8859-1")).find("Documento")
        assert doc is not None
        assert doc.findtext("Referencia/TpoDocRef") == "33"
        assert doc.findtext("Referencia/FolioRef") == "1042"


class TestEnvelope:
    def test_groups_documents_by_type(self) -> None:
        envelope = build_envelope(
            [("33", b"<DTE>a</DTE>"), ("33", b"<DTE>b</DTE>"), ("61", b"<DTE>c</DTE>")],
            EnvelopeData(
                issuer_rut=ISSUER_RUT,
                sender_rut=ISSUER_RUT,
                resolution_date="2026-01-01",
                resolution_number="80",
            ),
            now=NOW,
        )
        text = envelope.decode("iso-8859-1")
        assert "<RutReceptor>60803000-K</RutReceptor>" in text
        assert "<TpoDTE>33</TpoDTE><NroDTE>2</NroDTE>" in text
        assert "<TpoDTE>61</TpoDTE><NroDTE>1</NroDTE>" in text
        assert text.count("<DTE>") == 3


class TestResponses:
    def test_upload_accepted(self) -> None:
        raw = b"<RECEPCIONDTE><STATUS>0</STATUS><TRACKID>987654</TRACKID></RECEPCIONDTE>"
        result = parse_upload_response(raw)
        assert result.accepted_for_processing
        assert result.track_id == "987654"

    def test_upload_rejected_carries_the_detail(self) -> None:
        raw = b"<RECEPCIONDTE><STATUS>5</STATUS><GLOSA>Firma inconsistente</GLOSA></RECEPCIONDTE>"
        result = parse_upload_response(raw)
        assert not result.accepted_for_processing
        assert "Firma" in result.detail

    def test_status_epr_is_accepted(self) -> None:
        raw = b"<SII:RESPUESTA xmlns:SII='http://www.sii.cl/XMLSchema'><SII:RESP_HDR><ESTADO>EPR</ESTADO></SII:RESP_HDR></SII:RESPUESTA>"
        assert parse_status_response(raw).status == "accepted"

    def test_status_rct_is_rejected(self) -> None:
        raw = b"<RESPUESTA><ESTADO>RCT</ESTADO><GLOSA>Error de schema</GLOSA></RESPUESTA>"
        result = parse_status_response(raw)
        assert result.status == "rejected"
        assert "schema" in result.detail

    def test_unknown_state_stays_pending(self) -> None:
        raw = b"<RESPUESTA><ESTADO>SOK</ESTADO></RESPUESTA>"
        assert parse_status_response(raw).status == "pending"

    def test_garbage_stays_pending(self) -> None:
        assert parse_status_response(b"\x00\x01").status == "pending"


class TestBoleta:
    """DTE 39: la boleta tiene exigencias propias que la factura no tiene."""

    def test_boleta_declares_ind_servicio(self) -> None:
        """El esquema del SII lo exige en IdDoc para boletas; sin él, rechazo."""
        invoice = make_invoice(doc_type="39")
        xml = build_document(invoice, 77, "<TED/>").decode("iso-8859-1")
        root = ET.fromstring(xml)
        documento = root.find("Documento")
        assert documento is not None
        id_doc = documento.find("Encabezado/IdDoc")
        assert id_doc is not None
        assert id_doc.findtext("TipoDTE") == "39"
        assert id_doc.findtext("IndServicio") == "3"

    def test_an_invoice_does_not_carry_ind_servicio(self) -> None:
        invoice = make_invoice(doc_type="33")
        xml = build_document(invoice, 12, "<TED/>").decode("iso-8859-1")
        id_doc = ET.fromstring(xml).find("Documento/Encabezado/IdDoc")
        assert id_doc is not None
        assert id_doc.find("IndServicio") is None

    def test_boletas_travel_in_their_own_envelope(self) -> None:
        """Meter boletas en un EnvioDTE es un rechazo garantizado: no es el
        mismo sobre ni el mismo destino."""
        data = EnvelopeData(
            issuer_rut="76543210-K",
            sender_rut="76543210-K",
            resolution_date="2026-01-02",
            resolution_number="80",
        )
        payload = build_envelope([("39", b"<DTE/>")], data, kind="boleta")
        text = payload.decode("iso-8859-1")
        assert text.count("<EnvioBOLETA") == 1
        assert "EnvioDTE" not in text
        assert "<TpoDTE>39</TpoDTE>" in text

    def test_an_unknown_envelope_kind_is_refused(self) -> None:
        data = EnvelopeData(
            issuer_rut="76543210-K",
            sender_rut="76543210-K",
            resolution_date="2026-01-02",
            resolution_number="80",
        )
        with pytest.raises(ValueError):
            build_envelope([("39", b"<DTE/>")], data, kind="factura")
