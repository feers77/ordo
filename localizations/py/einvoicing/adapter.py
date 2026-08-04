"""Adaptador SIFEN: implementa el contrato del framework para Paraguay.

A diferencia de Chile, el SIFEN sí permite anular un DE aprobado mediante el
evento de cancelación (dentro del plazo legal), por eso
`supports_direct_cancellation` es verdadero.
"""

from __future__ import annotations

import secrets
from collections.abc import Callable

from localizations.py.einvoicing.cdc import CdcParts, build_cdc
from localizations.py.einvoicing.de import DeError, build_de
from localizations.py.einvoicing.responses import (
    parse_send_response,
    parse_status_response,
)
from modules.einvoicing.contracts import (
    FolioAssignment,
    InvoiceData,
    SendResult,
    StatusResult,
)
from ordo_core import taxid


def _random_security_code() -> int:
    return secrets.randbelow(1_000_000_000)


class SifenAdapter:
    country = "py"
    supports_direct_cancellation = True
    xml_encoding = "utf-8"

    def __init__(
        self,
        *,
        establishment: str = "001",
        expedition_point: str = "001",
        taxpayer_type: str = "2",
        security_code_provider: Callable[[], int] | None = None,
    ) -> None:
        self.establishment = establishment
        self.expedition_point = expedition_point
        self.taxpayer_type = taxpayer_type
        # Inyectable para que los tests sean deterministas; en producción es
        # aleatorio por documento, como exige el SIFEN.
        self._security_code = security_code_provider or _random_security_code

    def render(self, invoice: InvoiceData, folio: FolioAssignment) -> bytes:
        issuer_ruc = taxid.validate_ruc(invoice.issuer.tax_id)
        taxid.validate_ruc(invoice.receiver.tax_id)
        if not folio.authorization_code:
            raise DeError(
                "PY_DE_NO_TIMBRADO",
                "El rango de numeración no trae el timbrado del SIFEN",
                hint="Carga el número de timbrado en authorization_code del rango.",
            )
        number, _, dv = issuer_ruc.partition("-")
        security_code = self._security_code()
        cdc = build_cdc(
            CdcParts(
                document_type=invoice.document_type_code,
                issuer_ruc=number,
                issuer_ruc_dv=dv,
                establishment=self.establishment,
                expedition_point=self.expedition_point,
                document_number=folio.number,
                taxpayer_type=self.taxpayer_type,
                issue_date=invoice.issue_date,
                emission_type="1",
                security_code=security_code,
            )
        )
        return build_de(
            invoice,
            cdc=cdc,
            timbrado=folio.authorization_code,
            establishment=self.establishment,
            expedition_point=self.expedition_point,
            document_number=folio.number,
            emission_type="1",
            security_code=security_code,
        )

    def parse_send_response(self, raw: bytes) -> SendResult:
        return parse_send_response(raw)

    def parse_status_response(self, raw: bytes) -> StatusResult:
        return parse_status_response(raw)
