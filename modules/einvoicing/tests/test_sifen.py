"""Adaptador SIFEN: CDC, XML del DE, QR y respuestas."""

import xml.etree.ElementTree as ET
from datetime import date
from decimal import Decimal

import pytest
from hypothesis import given
from hypothesis import strategies as st
from localizations.py.einvoicing.adapter import SifenAdapter
from localizations.py.einvoicing.cdc import (
    CdcError,
    CdcParts,
    build_cdc,
    check_digit,
    validate_cdc,
)
from localizations.py.einvoicing.de import DeError
from localizations.py.einvoicing.qr import build_qr_params
from localizations.py.einvoicing.responses import (
    parse_send_response,
    parse_status_response,
)
from ordo_core.taxid import ruc_check_digit

from modules.account.taxes import Tax, compute_document
from modules.einvoicing.contracts import (
    FolioAssignment,
    InvoiceData,
    InvoiceLine,
    Party,
)

IVA10 = Tax(code="IVA10", name="IVA 10%", amount=Decimal("10"))


def ruc(number: int) -> str:
    return f"{number}-{ruc_check_digit(number)}"


ISSUER_RUC = ruc(80012345)
RECEIVER_RUC = ruc(80054321)


def make_parts(**overrides: object) -> CdcParts:
    values: dict = {
        "document_type": "1",
        "issuer_ruc": "80012345",
        "issuer_ruc_dv": ISSUER_RUC.rsplit("-", 1)[1],
        "establishment": "001",
        "expedition_point": "001",
        "document_number": 1042,
        "taxpayer_type": "2",
        "issue_date": date(2026, 8, 4),
        "emission_type": "1",
        "security_code": 123456789,
    }
    values.update(overrides)
    return CdcParts(**values)


def make_invoice(*, doc_type: str = "1") -> InvoiceData:
    lines = [
        {"price_unit": Decimal("1000000"), "quantity": Decimal("2"), "taxes": [IVA10]},
    ]
    return InvoiceData(
        document_type_code=doc_type,
        issue_date=date(2026, 8, 4),
        issuer=Party(tax_id=ISSUER_RUC, name="ACME SA", address="Asunción"),
        receiver=Party(tax_id=RECEIVER_RUC, name="Cliente SRL"),
        lines=(
            InvoiceLine(
                description="Servicio mensual",
                quantity=Decimal("2"),
                price_unit=Decimal("1000000"),
            ),
        ),
        taxes=compute_document(lines, decimals=0),
        currency="PYG",
    )


class TestCdc:
    def test_builds_44_digits_and_validates(self) -> None:
        cdc = build_cdc(make_parts())
        assert len(cdc) == 44
        assert cdc.isdigit()
        assert validate_cdc(cdc) == cdc

    def test_embeds_the_parts_in_order(self) -> None:
        cdc = build_cdc(make_parts())
        assert cdc.startswith("01")  # tipo de DE
        assert cdc[2:10] == "80012345"  # RUC emisor
        assert cdc[11:14] == "001"  # establecimiento
        assert cdc[17:24] == "0001042"  # número de documento
        assert cdc[25:33] == "20260804"  # fecha

    @given(st.integers(min_value=0, max_value=999_999_999))
    def test_check_digit_is_stable(self, security_code: int) -> None:
        cdc = build_cdc(make_parts(security_code=security_code))
        assert validate_cdc(cdc) == cdc

    def test_tampering_breaks_the_check_digit(self) -> None:
        cdc = build_cdc(make_parts())
        flipped = ("1" if cdc[20] != "1" else "2") + cdc[21:]
        tampered = cdc[:20] + flipped
        with pytest.raises(CdcError) as excinfo:
            validate_cdc(tampered)
        assert excinfo.value.code == "PY_CDC_BAD_CHECK_DIGIT"

    def test_number_beyond_seven_digits_is_rejected(self) -> None:
        with pytest.raises(CdcError) as excinfo:
            build_cdc(make_parts(document_number=10_000_000))
        assert excinfo.value.code == "PY_CDC_BAD_NUMBER"

    def test_check_digit_matches_module_11(self) -> None:
        # 11 - (suma de dígito*factor % 11); ejemplo calculado a mano con "1".
        assert check_digit("1") == str(11 - (1 * 2) % 11)


class TestDe:
    def make_adapter(self) -> SifenAdapter:
        return SifenAdapter(security_code_provider=lambda: 123456789)

    def test_renders_a_complete_de(self) -> None:
        xml = self.make_adapter().render(
            make_invoice(), FolioAssignment(number=1042, authorization_code="12345678")
        )
        root = ET.fromstring(xml.decode())
        de = root.find("DE")
        assert de is not None
        assert validate_cdc(de.attrib["Id"])
        assert de.findtext("gTimb/dNumTim") == "12345678"
        assert de.findtext("gTimb/dNumDoc") == "0001042"
        assert de.findtext("gDatGralOpe/gEmis/dRucEm") == "80012345"
        assert de.findtext("gTotSub/dSub10") == "2000000"
        assert de.findtext("gTotSub/dIVA10") == "200000"
        assert de.findtext("gTotSub/dTotIVA") == "200000"
        assert de.findtext("gTotSub/dTotGralOpe") == "2200000"

    def test_missing_timbrado_is_an_error(self) -> None:
        with pytest.raises(DeError) as excinfo:
            self.make_adapter().render(make_invoice(), FolioAssignment(number=1))
        assert excinfo.value.code == "PY_DE_NO_TIMBRADO"

    def test_unknown_iva_rate_is_rejected(self) -> None:
        weird = Tax(code="IVA21", name="IVA 21%", amount=Decimal("21"))
        lines = [{"price_unit": Decimal("1000"), "quantity": Decimal("1"), "taxes": [weird]}]
        invoice = make_invoice()
        bad = InvoiceData(
            document_type_code=invoice.document_type_code,
            issue_date=invoice.issue_date,
            issuer=invoice.issuer,
            receiver=invoice.receiver,
            lines=invoice.lines,
            taxes=compute_document(lines, decimals=0),
            currency="PYG",
        )
        with pytest.raises(DeError) as excinfo:
            self.make_adapter().render(
                bad, FolioAssignment(number=1, authorization_code="12345678")
            )
        assert excinfo.value.code == "PY_DE_UNKNOWN_RATE"


class TestQr:
    def test_url_is_deterministic_and_signed_with_the_csc(self) -> None:
        kwargs = {
            "cdc": build_cdc(make_parts()),
            "issue_date_iso": "2026-08-04",
            "receiver_ruc": "80054321",
            "total": Decimal("2200000"),
            "total_iva": Decimal("200000"),
            "item_count": 1,
            "digest_value": "abc123",
            "csc_id": "0001",
        }
        url_a = build_qr_params(csc="secreto", **kwargs)
        url_b = build_qr_params(csc="secreto", **kwargs)
        url_c = build_qr_params(csc="otro", **kwargs)
        assert url_a == url_b
        assert url_a != url_c  # el hash depende del CSC
        assert url_a.startswith("https://ekuatia.set.gov.py/consultas/qr?nVersion=150")
        assert "cHashQR=" in url_a
        assert "secreto" not in url_a  # el CSC firma pero nunca viaja


class TestResponses:
    def test_approved_de(self) -> None:
        raw = (
            b"<rResEnviDe><dCodRes>0260</dCodRes><dProtAut>777</dProtAut>"
            b"<dMsgRes>Autorizado</dMsgRes></rResEnviDe>"
        )
        result = parse_send_response(raw)
        assert result.accepted_for_processing
        assert result.track_id == "777"

    def test_batch_received_keeps_processing(self) -> None:
        raw = b"<r><dCodRes>0300</dCodRes><dProtConsLote>555</dProtConsLote></r>"
        result = parse_send_response(raw)
        assert result.accepted_for_processing
        assert result.track_id == "555"

    def test_rejection_carries_the_message(self) -> None:
        raw = b"<r><dCodRes>1001</dCodRes><dMsgRes>CDC duplicado</dMsgRes></r>"
        status = parse_status_response(raw)
        assert status.status == "rejected"
        assert "duplicado" in status.detail

    def test_unknown_code_stays_pending(self) -> None:
        assert parse_status_response(b"<r><dCodRes>0299</dCodRes></r>").status == "pending"
