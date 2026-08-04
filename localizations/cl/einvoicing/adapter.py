"""Adaptador SII: implementa el contrato del framework para Chile.

Un DTE aceptado no se anula ante el SII: se corrige con una nota de crédito
(tipo 61). Por eso `supports_direct_cancellation` es falso.
"""

from __future__ import annotations

from datetime import datetime

from localizations.cl.einvoicing.caf import parse_caf
from localizations.cl.einvoicing.dte import DteError, build_document
from localizations.cl.einvoicing.responses import (
    parse_status_response,
    parse_upload_response,
)
from localizations.cl.einvoicing.ted import build_ted
from modules.einvoicing.contracts import (
    FolioAssignment,
    InvoiceData,
    SendResult,
    StatusResult,
)
from ordo_core import taxid


class SiiAdapter:
    country = "cl"
    supports_direct_cancellation = False
    xml_encoding = "iso-8859-1"  # el formato DTE del SII lo exige

    def __init__(self, *, now: datetime | None = None) -> None:
        # `now` inyectable para que los tests produzcan bytes deterministas.
        self._now = now

    def render(self, invoice: InvoiceData, folio: FolioAssignment) -> bytes:
        taxid.validate_rut(invoice.issuer.tax_id)
        taxid.validate_rut(invoice.receiver.tax_id)
        if not folio.authorization_code:
            raise DteError(
                "CL_DTE_NO_CAF",
                "El rango de folios no trae el CAF: sin él no hay timbre",
                hint="Carga el XML del CAF en authorization_code del rango.",
            )
        caf = parse_caf(folio.authorization_code)
        ted = build_ted(invoice, folio.number, caf, now=self._now)
        return build_document(invoice, folio.number, ted)

    def parse_send_response(self, raw: bytes) -> SendResult:
        return parse_upload_response(raw)

    def parse_status_response(self, raw: bytes) -> StatusResult:
        return parse_status_response(raw)
