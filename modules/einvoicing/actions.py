"""Acciones de facturación electrónica expuestas a la API.

Firmar, enviar y consultar acuse no se exponen todavía: requieren la clave
en el vault y transporte contra el ambiente de certificación. Lo que sí se
expone es todo lo que no depende de secretos: emitir el XML con folio,
declarar contingencia y anular.
"""

from __future__ import annotations

from typing import Any

from ordo_core.actions import action
from ordo_core.environment import Environment

from modules.einvoicing.bridge import invoice_data_from_sale
from modules.einvoicing.runtime import default_registry
from modules.einvoicing.services import EinvoicingService
from modules.einvoicing.statemachine import EdiError


def _service(env: Environment) -> EinvoicingService:
    return EinvoicingService(env, default_registry())


@action(
    "sale.order",
    "action_einvoice",
    summary="Emite el documento electrónico de la orden: folio asignado y XML generado",
    requires_approval=True,
    params={"document_type_code": "Tipo de documento del país (33 en CL, 1 en PY)"},
)
async def einvoice(env: Environment, record_id: int, params: dict[str, Any]) -> dict[str, Any]:
    document_type = str(params.get("document_type_code", "")).strip()
    if not document_type:
        raise EdiError(
            "EDI_DOCUMENT_TYPE_REQUIRED",
            "Falta el tipo de documento a emitir",
            hint="Pasa params.document_type_code, por ejemplo '33' en Chile.",
        )
    invoice, country, company_id = await invoice_data_from_sale(
        env, record_id, document_type_code=document_type
    )
    service = _service(env)
    document_id = await service.create_document(
        country_code=country,
        document_type_code=document_type,
        company_id=company_id,
        partner_id=None,
    )
    number = await service.action_generate(document_id, invoice)
    return {"document_id": document_id, "number": number, "state": "generated"}


@action(
    "edi.document",
    "action_contingency",
    summary="Declara contingencia: la autoridad no responde y se reenviará después",
)
async def contingency(env: Environment, record_id: int, params: dict[str, Any]) -> dict[str, Any]:
    await _service(env).action_contingency(record_id)
    return {"state": "contingency"}


@action(
    "edi.document",
    "action_cancel",
    summary="Anula el documento si el país y el estado lo permiten",
    requires_approval=True,
)
async def cancel(env: Environment, record_id: int, params: dict[str, Any]) -> dict[str, Any]:
    await _service(env).action_cancel(record_id)
    return {"state": "cancelled"}
