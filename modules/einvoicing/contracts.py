"""Contratos entre la contabilidad y los adaptadores de país.

`InvoiceData` es deliberadamente neutro: el adaptador chileno y el paraguayo
reciben lo mismo y cada uno produce su XML. Si un país necesita un dato que
no está aquí, se agrega al contrato con nombre neutro, no se cuela un campo
con nombre de formulario local.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from typing import Protocol

from modules.account.taxes import TaxResult


@dataclass(frozen=True)
class Party:
    """Emisor o receptor del documento."""

    tax_id: str
    name: str
    address: str = ""
    city: str = ""
    activity: str = ""
    email: str = ""


@dataclass(frozen=True)
class InvoiceLine:
    description: str
    quantity: Decimal
    price_unit: Decimal
    discount_percent: Decimal = Decimal("0")
    exempt: bool = False


@dataclass(frozen=True)
class InvoiceData:
    """Todo lo que un adaptador necesita para materializar el documento."""

    document_type_code: str
    issue_date: date
    issuer: Party
    receiver: Party
    lines: tuple[InvoiceLine, ...]
    taxes: TaxResult
    currency: str
    reference_document: str = ""
    reference_reason: str = ""


@dataclass(frozen=True)
class FolioAssignment:
    """Número autorizado más la autorización que lo respalda."""

    number: int
    authorization_code: str = ""


@dataclass(frozen=True)
class SendResult:
    """Lo que la autoridad contesta al recibir un envío."""

    accepted_for_processing: bool
    track_id: str = ""
    detail: str = ""


@dataclass(frozen=True)
class StatusResult:
    """Veredicto de la autoridad sobre un documento ya enviado."""

    status: str  # accepted | rejected | pending
    detail: str = ""
    raw: str = ""


@dataclass(frozen=True)
class SignedXml:
    payload: bytes
    signature_reference: str = ""


class Signer(Protocol):
    """Firma un XML completo. La implementación productiva depende de ADR-014."""

    def sign(self, xml: bytes, reference: str) -> bytes: ...


class Transport(Protocol):
    """Lleva bytes a la autoridad y trae bytes de vuelta. Sin red en tests."""

    async def send(self, payload: bytes) -> bytes: ...


class EinvoiceAdapter(Protocol):
    """Lo que cada país implementa. El framework no conoce formatos locales."""

    country: str
    supports_direct_cancellation: bool

    def render(self, invoice: InvoiceData, folio: FolioAssignment) -> bytes:
        """Construye el XML del documento, sin firmar."""
        ...

    def parse_send_response(self, raw: bytes) -> SendResult: ...

    def parse_status_response(self, raw: bytes) -> StatusResult: ...


@dataclass
class AdapterRegistry:
    """Adaptadores disponibles por país, inyectados al armar la aplicación."""

    adapters: dict[str, EinvoiceAdapter] = field(default_factory=dict)

    def register(self, adapter: EinvoiceAdapter) -> None:
        self.adapters[adapter.country] = adapter

    def get(self, country: str) -> EinvoiceAdapter | None:
        return self.adapters.get(country)
