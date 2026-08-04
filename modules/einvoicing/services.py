"""Servicios de facturación electrónica: folios y ciclo de vida del documento.

Ninguna acción escribe `state` directo: todas pasan por la tabla de
transiciones de `statemachine.py`. El adaptador de país se inyecta; este
servicio no sabe qué es un DTE ni un DE.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from typing import Any

from ordo_core.environment import Environment
from ordo_core.recordset import RecordSet

from modules.einvoicing.contracts import (
    AdapterRegistry,
    FolioAssignment,
    InvoiceData,
    Signer,
    Transport,
)
from modules.einvoicing.statemachine import EdiError, next_state


class FolioService:
    """Asigna números desde los rangos autorizados (CAF chileno, timbrado paraguayo)."""

    def __init__(self, env: Environment) -> None:
        self.env = env
        self.ranges = RecordSet(env, "edi.folio.range")

    async def assign(
        self,
        *,
        country_code: str,
        document_type_code: str,
        company_id: int,
        on_date: date | None = None,
    ) -> FolioAssignment:
        result = await self.ranges.search(
            [
                ("country_code", "=", country_code),
                ("document_type_code", "=", document_type_code),
                ("company_id", "=", company_id),
            ],
            fields=["id", "range_to", "next_number", "authorization_code", "valid_until"],
        )
        today = on_date or datetime.now(tz=UTC).date()
        exhausted = False
        expired = False
        for row in sorted(result["rows"], key=lambda item: item["id"]):
            if row["next_number"] > row["range_to"]:
                exhausted = True
                continue
            valid_until = row["valid_until"]
            if valid_until is not None and _as_date(valid_until) < today:
                expired = True
                continue
            number = int(row["next_number"])
            await self.ranges.write([row["id"]], {"next_number": number + 1})
            return FolioAssignment(
                number=number, authorization_code=row["authorization_code"] or ""
            )
        if expired and not exhausted:
            raise EdiError(
                "EDI_FOLIO_EXPIRED",
                f"La autorización para el tipo {document_type_code} de {country_code} está vencida",
                hint="Solicita un rango nuevo a la autoridad fiscal y cárgalo.",
            )
        raise EdiError(
            "EDI_FOLIO_EXHAUSTED",
            f"No quedan folios disponibles para el tipo {document_type_code} de {country_code}",
            hint="Carga un rango autorizado nuevo en edi.folio.range.",
        )


def _as_date(value: Any) -> date:
    if isinstance(value, date):
        return value
    return date.fromisoformat(str(value))


class EinvoicingService:
    def __init__(self, env: Environment, registry: AdapterRegistry) -> None:
        self.env = env
        self.registry = registry
        self.documents = RecordSet(env, "edi.document")
        self.folios = FolioService(env)

    async def create_document(
        self,
        *,
        country_code: str,
        document_type_code: str,
        company_id: int,
        move_id: int | None = None,
        partner_id: int | None = None,
    ) -> int:
        self._adapter(country_code)
        [document_id] = await self.documents.create(
            [
                {
                    "country_code": country_code,
                    "document_type_code": document_type_code,
                    "company_id": company_id,
                    "move_id": move_id,
                    "partner_id": partner_id,
                    "state": "draft",
                }
            ]
        )
        return document_id

    async def action_generate(self, document_id: int, invoice: InvoiceData) -> int:
        """Asigna folio y construye el XML. Devuelve el folio asignado.

        Regenerar tras un rechazo toma un folio nuevo: el SII y el SIFEN no
        aceptan reutilizar numeración de un documento rechazado con otro
        contenido.
        """
        document = await self._get(document_id)
        target = next_state(document["state"], "generate")
        adapter = self._adapter(document["country_code"])

        folio = await self.folios.assign(
            country_code=document["country_code"],
            document_type_code=document["document_type_code"],
            company_id=document["company_id"],
            on_date=invoice.issue_date,
        )
        xml = adapter.render(invoice, folio)
        encoding = getattr(adapter, "xml_encoding", "utf-8")
        await self.documents.write(
            [document_id],
            {
                "state": target,
                "number": folio.number,
                "xml_payload": xml.decode(encoding, errors="replace"),
                "payload_encoding": encoding,
                "error_message": None,
            },
        )
        return folio.number

    async def action_sign(self, document_id: int, signer: Signer) -> None:
        document = await self._get(document_id)
        target = next_state(document["state"], "sign")
        payload = (document["xml_payload"] or "").encode(
            document["payload_encoding"] or "utf-8", errors="replace"
        )
        if not payload:
            raise EdiError(
                "EDI_NOT_GENERATED",
                "No hay XML que firmar: genera el documento primero",
                hint="Llama a action_generate antes de firmar.",
            )
        signed = signer.sign(payload, reference=str(document["number"]))
        await self.documents.write(
            [document_id],
            {
                "state": target,
                "xml_payload": signed.decode(
                    document["payload_encoding"] or "utf-8", errors="replace"
                ),
            },
        )

    async def action_send(self, document_id: int, transport: Transport) -> str:
        """Envía el XML firmado y guarda el track id. Devuelve el track id."""
        document = await self._get(document_id)
        target = next_state(document["state"], "send")
        adapter = self._adapter(document["country_code"])

        raw = await transport.send(
            (document["xml_payload"] or "").encode(
                document["payload_encoding"] or "utf-8", errors="replace"
            )
        )
        result = adapter.parse_send_response(raw)
        values: dict[str, Any] = {
            "state": target,
            "attempts": int(document["attempts"] or 0) + 1,
            "sent_at": datetime.now(tz=UTC),
            "response_payload": raw.decode("utf-8", errors="replace"),
            "contingency": False,
        }
        if result.accepted_for_processing:
            values["track_id"] = result.track_id
        else:
            values["error_message"] = result.detail
        await self.documents.write([document_id], values)
        if not result.accepted_for_processing:
            # Recibido pero no aceptado a trámite: la autoridad contestó con
            # error de recepción; el documento queda en `sent` con el detalle
            # y el veredicto llegará (o se corregirá) vía action_check.
            return ""
        return result.track_id

    async def action_check(self, document_id: int, transport: Transport) -> str:
        """Consulta el veredicto y aplica accept/reject. Devuelve el estado final."""
        document = await self._get(document_id)
        adapter = self._adapter(document["country_code"])
        raw = await transport.send((document["track_id"] or "").encode())
        status = adapter.parse_status_response(raw)
        if status.status == "pending":
            return document["state"]
        action = "accept" if status.status == "accepted" else "reject"
        target = next_state(document["state"], action)
        values: dict[str, Any] = {"state": target, "response_payload": status.raw or None}
        if status.status == "rejected":
            values["error_message"] = status.detail
        await self.documents.write([document_id], values)
        return target

    async def action_contingency(self, document_id: int) -> None:
        """Marca el documento para emisión en contingencia: se reenvía después."""
        document = await self._get(document_id)
        target = next_state(document["state"], "contingency")
        await self.documents.write([document_id], {"state": target, "contingency": True})

    async def action_cancel(self, document_id: int) -> None:
        document = await self._get(document_id)
        if document["state"] == "accepted":
            adapter = self._adapter(document["country_code"])
            if not adapter.supports_direct_cancellation:
                raise EdiError(
                    "EDI_CANCEL_UNSUPPORTED",
                    "Este país no anula documentos aceptados: emite una nota "
                    "de crédito que lo revierta",
                    hint="En Chile, un DTE aceptado se corrige con NC (tipo 61).",
                )
        target = next_state(document["state"], "cancel")
        await self.documents.write([document_id], {"state": target})

    # -- internos ---------------------------------------------------------

    def _adapter(self, country_code: str) -> Any:
        adapter = self.registry.get(country_code)
        if adapter is None:
            raise EdiError(
                "EDI_NO_ADAPTER",
                f"No hay adaptador de facturación electrónica para '{country_code}'",
                hint="Registra el adaptador del país en el AdapterRegistry.",
            )
        return adapter

    async def _get(self, document_id: int) -> dict[str, Any]:
        rows = await self.documents.read(
            [document_id],
            fields=[
                "id",
                "state",
                "country_code",
                "document_type_code",
                "number",
                "xml_payload",
                "payload_encoding",
                "track_id",
                "attempts",
                "company_id",
            ],
        )
        if not rows:
            raise EdiError("EDI_DOCUMENT_NOT_FOUND", f"No existe el documento {document_id}")
        return rows[0]
