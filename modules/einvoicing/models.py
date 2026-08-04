"""Modelos de facturación electrónica: documentos, certificados y folios.

El documento electrónico es una máquina de estados, no un registro que se
edita: cada transición pasa por un método explícito del servicio. El estado
`contingency` existe porque las autoridades fiscales se caen, y la ley ya
prevé qué hacer cuando pasa.
"""

from ordo_core.fields import (
    Boolean,
    Char,
    Date,
    Datetime,
    Integer,
    Many2one,
    Selection,
    Text,
)
from ordo_core.model import Model

EDI_STATES = [
    ("draft", "Borrador"),
    ("generated", "XML generado"),
    ("signed", "Firmado"),
    ("sent", "Enviado"),
    ("accepted", "Aceptado"),
    ("rejected", "Rechazado"),
    ("contingency", "Contingencia"),
    ("cancelled", "Anulado"),
]


class EdiDocument(Model):
    _name = "edi.document"
    _description = "Documento tributario electrónico"

    country_code = Char(
        required=True,
        index=True,
        agent_hint="País cuyo adaptador emite el documento, en ISO 3166-1 alfa-2 minúscula",
        examples=["cl", "py"],
    )
    document_type_code = Char(
        required=True,
        index=True,
        agent_hint=(
            "Código del tipo de documento según la autoridad fiscal: DTE del SII "
            "en Chile (33, 39, 61...), tipo de DE del SIFEN en Paraguay (1, 5, 6...)"
        ),
        examples=["33", "1"],
    )
    number = Integer(
        agent_hint=(
            "Folio o número asignado desde el rango autorizado. Se toma al generar "
            "el XML y no se recicla: un folio quemado queda quemado"
        ),
        examples=["1042"],
    )
    state = Selection(
        EDI_STATES,
        default="draft",
        agent_hint=(
            "Estado del documento. Nunca se escribe directo: usa las acciones "
            "action_generate, action_sign, action_send, action_accept, "
            "action_reject, action_contingency y action_cancel"
        ),
        examples=["draft", "accepted"],
    )
    move_id = Many2one(
        "account.move",
        index=True,
        agent_hint="Asiento contable que respalda el documento, si ya existe",
        examples=["17"],
    )
    partner_id = Many2one(
        "res.partner",
        agent_hint="Receptor del documento",
        examples=["7"],
    )
    xml_payload = Text(
        agent_hint="XML del documento tal como se firmó o se firmará",
        examples=['<DTE version="1.0">...</DTE>'],
    )
    track_id = Char(
        index=True,
        agent_hint=(
            "Identificador que devuelve la autoridad al recibir el envío: TrackID "
            "del SII, número de protocolo del SIFEN"
        ),
        examples=["123456789"],
    )
    response_payload = Text(
        agent_hint="Última respuesta cruda de la autoridad fiscal, para auditoría",
        examples=["<RECEPCIONDTE>...</RECEPCIONDTE>"],
    )
    error_message = Text(
        agent_hint="Detalle del rechazo o del error de envío, si lo hubo",
        examples=["RUT receptor no corresponde"],
    )
    attempts = Integer(
        default=0,
        agent_hint="Cantidad de envíos intentados, incluidos los de contingencia",
        examples=["1", "3"],
    )
    contingency = Boolean(
        default=False,
        agent_hint=(
            "Verdadero si el documento se emitió en modo contingencia porque la "
            "autoridad no estaba disponible; debe reenviarse al recuperarse"
        ),
        examples=["false"],
    )
    sent_at = Datetime(
        agent_hint="Momento UTC del último envío",
        examples=["2026-08-04T12:00:00Z"],
    )
    company_id = Many2one(
        "res.company", required=True, agent_hint="Compañía emisora", examples=["1"]
    )


class EdiCertificate(Model):
    _name = "edi.certificate"
    _description = "Certificado de firma electrónica (metadatos)"

    name = Char(
        required=True,
        agent_hint="Nombre descriptivo del certificado",
        examples=["Certificado SII producción 2026"],
    )
    country_code = Char(
        required=True,
        index=True,
        agent_hint="País donde el certificado tiene validez fiscal",
        examples=["cl"],
    )
    subject = Char(
        agent_hint="Subject del certificado X.509",
        examples=["CN=ACME SpA, serialNumber=76543210-K"],
    )
    serial = Char(
        agent_hint="Número de serie del certificado",
        examples=["4F:2A:11"],
    )
    valid_from = Date(
        agent_hint="Inicio de vigencia",
        examples=["2026-01-01"],
    )
    valid_to = Date(
        index=True,
        agent_hint="Fin de vigencia; un certificado vencido no firma",
        examples=["2027-01-01"],
    )
    vault_ref = Char(
        required=True,
        agent_hint=(
            "Referencia al secreto en el vault que contiene la clave privada. La "
            "clave nunca se guarda en la base de datos (AGENTS.md §7)"
        ),
        examples=["vault://edi/cl/acme-2026"],
    )
    active = Boolean(default=True, agent_hint="Certificado disponible", examples=["true"])
    company_id = Many2one("res.company", required=True, agent_hint="Compañía dueña", examples=["1"])


class EdiFolioRange(Model):
    _name = "edi.folio.range"
    _description = "Rango de numeración autorizado (CAF, timbrado)"

    country_code = Char(
        required=True,
        index=True,
        agent_hint="País que autorizó el rango",
        examples=["cl", "py"],
    )
    document_type_code = Char(
        required=True,
        index=True,
        agent_hint="Tipo de documento al que aplica el rango",
        examples=["33", "1"],
    )
    range_from = Integer(
        required=True,
        agent_hint="Primer número autorizado del rango",
        examples=["1"],
    )
    range_to = Integer(
        required=True,
        agent_hint="Último número autorizado del rango, inclusive",
        examples=["500"],
    )
    next_number = Integer(
        required=True,
        agent_hint="Próximo número a asignar; al superar range_to el rango se agotó",
        examples=["43"],
    )
    authorization_code = Text(
        agent_hint=(
            "Autorización de la autoridad: el XML del CAF en Chile, el número de "
            "timbrado en Paraguay"
        ),
        examples=["<AUTORIZACION>...</AUTORIZACION>", "12345678"],
    )
    valid_until = Date(
        agent_hint="Vencimiento de la autorización; un rango vencido no asigna folios",
        examples=["2027-02-01"],
    )
    active = Boolean(default=True, agent_hint="Rango disponible", examples=["true"])
    company_id = Many2one(
        "res.company", required=True, agent_hint="Compañía autorizada", examples=["1"]
    )
