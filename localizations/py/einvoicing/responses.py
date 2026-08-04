"""Lectura de las respuestas del SIFEN.

El SIFEN contesta con códigos numéricos: 0260 es aprobado, 0300 y la familia
1000+ son rechazos con detalle. Igual que con el SII, lo no reconocido queda
pendiente y se vuelve a consultar.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET

from modules.einvoicing.contracts import SendResult, StatusResult

APPROVED = {"0260"}  # "Autorizado el DE"
APPROVED_WITH_NOTES = {"0261", "0262"}  # autorizado con observaciones
REJECTED_PREFIXES = ("03", "04", "1")


def _find_text(root: ET.Element, *tags: str) -> str:
    for node in root.iter():
        local = node.tag.rsplit("}", 1)[-1]
        if local in tags and node.text:
            return node.text.strip()
    return ""


def parse_send_response(raw: bytes) -> SendResult:
    try:
        root = ET.fromstring(raw.decode("utf-8", errors="replace"))  # noqa: S314
    except ET.ParseError:
        return SendResult(
            accepted_for_processing=False,
            detail="La respuesta del SIFEN no es XML legible",
        )
    code = _find_text(root, "dCodRes")
    message = _find_text(root, "dMsgRes")
    protocol = _find_text(root, "dProtAut", "dProtConsLote", "dId")
    if code in APPROVED | APPROVED_WITH_NOTES or (code == "0300" and protocol):
        # 0300 en el envío por lote significa "lote recibido": el veredicto
        # por documento llega en la consulta posterior.
        return SendResult(accepted_for_processing=True, track_id=protocol, detail=message)
    return SendResult(accepted_for_processing=False, detail=message or code or "sin código")


def parse_status_response(raw: bytes) -> StatusResult:
    text = raw.decode("utf-8", errors="replace")
    try:
        root = ET.fromstring(text)  # noqa: S314
    except ET.ParseError:
        return StatusResult(status="pending", detail="Respuesta ilegible; reintentar")
    code = _find_text(root, "dCodRes")
    message = _find_text(root, "dMsgRes")
    if code in APPROVED | APPROVED_WITH_NOTES:
        return StatusResult(status="accepted", detail=message, raw=text)
    if code.startswith(REJECTED_PREFIXES) and code not in {"0300"}:
        return StatusResult(status="rejected", detail=message or code, raw=text)
    return StatusResult(status="pending", detail=message or code, raw=text)
