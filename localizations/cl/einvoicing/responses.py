"""Lectura de las respuestas del SII: recepción de envío y estado.

Los códigos de estado son los publicados por el SII para la consulta de
estado de envío. Un código no listado se trata como pendiente: ante la duda
no se marca aceptado ni rechazado, se vuelve a preguntar.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET

from modules.einvoicing.contracts import SendResult, StatusResult

ACCEPTED_STATES = {"EPR", "DOK", "ACD"}
REJECTED_STATES = {"RCT", "RCH", "RFR", "RSC", "DNK", "RPR"}


def _find_text(root: ET.Element, *tags: str) -> str:
    """Busca el primer tag que exista, ignorando namespaces del SII."""
    for node in root.iter():
        local = node.tag.rsplit("}", 1)[-1]
        if local in tags and node.text:
            return node.text.strip()
    return ""


def parse_upload_response(raw: bytes) -> SendResult:
    """Respuesta a la subida del EnvioDTE: STATUS 0 más TRACKID es recibido."""
    try:
        root = ET.fromstring(raw.decode("utf-8", errors="replace"))  # noqa: S314
    except ET.ParseError:
        return SendResult(
            accepted_for_processing=False,
            detail="La respuesta del SII no es XML legible",
        )
    status = _find_text(root, "STATUS")
    track_id = _find_text(root, "TRACKID")
    if status == "0" and track_id:
        return SendResult(accepted_for_processing=True, track_id=track_id)
    detail = _find_text(root, "GLOSA", "DETAIL") or f"STATUS={status or 'desconocido'}"
    return SendResult(accepted_for_processing=False, detail=detail)


def parse_status_response(raw: bytes) -> StatusResult:
    """Consulta de estado del envío: EPR y compañía aceptan, RCT y familia rechazan."""
    text = raw.decode("utf-8", errors="replace")
    try:
        root = ET.fromstring(text)  # noqa: S314
    except ET.ParseError:
        return StatusResult(status="pending", detail="Respuesta ilegible; reintentar")
    state = _find_text(root, "ESTADO")
    detail = _find_text(root, "GLOSA", "GLOSA_ESTADO")
    if state in ACCEPTED_STATES:
        return StatusResult(status="accepted", detail=detail, raw=text)
    if state in REJECTED_STATES:
        return StatusResult(status="rejected", detail=detail or state, raw=text)
    return StatusResult(status="pending", detail=detail or state, raw=text)
