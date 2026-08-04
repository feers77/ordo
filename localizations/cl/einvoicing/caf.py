"""Lectura del CAF (Código de Autorización de Folios) del SII.

El CAF es el XML que el SII entrega al autorizar un rango de folios. Trae
los datos de la autorización (`DA`) y una clave privada RSA (`RSASK`) con la
que el emisor firma el timbre (TED) de cada documento del rango.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from dataclasses import dataclass

from ordo_core.errors import KernelError


class CafError(KernelError):
    """CAF ilegible o incompleto."""


@dataclass(frozen=True)
class Caf:
    issuer_rut: str
    issuer_name: str
    document_type: str
    range_from: int
    range_to: int
    authorization_date: str
    private_key_pem: str
    caf_xml: str  # el elemento <CAF> tal cual, para incrustarlo en el TED

    def covers(self, folio: int) -> bool:
        return self.range_from <= folio <= self.range_to


def _text(root: ET.Element, path: str, where: str) -> str:
    node = root.find(path)
    if node is None or node.text is None:
        raise CafError("CL_CAF_INCOMPLETE", f"El CAF no trae '{path}' en {where}")
    return node.text.strip()


def parse_caf(raw: str) -> Caf:
    """Extrae los datos del `<AUTORIZACION>` que entrega el SII."""
    try:
        root = ET.fromstring(raw)  # noqa: S314 — XML propio, emitido por el SII
    except ET.ParseError as exc:
        raise CafError("CL_CAF_INVALID_XML", "El CAF no es XML válido") from exc

    caf = root.find("CAF") if root.tag == "AUTORIZACION" else root
    if caf is None or caf.tag != "CAF":
        raise CafError("CL_CAF_INCOMPLETE", "No se encontró el elemento CAF")

    key_node = root.find("RSASK")
    if key_node is None or not (key_node.text or "").strip():
        raise CafError(
            "CL_CAF_NO_KEY",
            "El CAF no trae la clave privada RSASK",
            hint="Descarga el archivo completo desde el SII, no solo el elemento CAF.",
        )

    return Caf(
        issuer_rut=_text(caf, "DA/RE", "DA"),
        issuer_name=_text(caf, "DA/RS", "DA"),
        document_type=_text(caf, "DA/TD", "DA"),
        range_from=int(_text(caf, "DA/RNG/D", "RNG")),
        range_to=int(_text(caf, "DA/RNG/H", "RNG")),
        authorization_date=_text(caf, "DA/FA", "DA"),
        private_key_pem=(key_node.text or "").strip(),
        caf_xml=ET.tostring(caf, encoding="unicode"),
    )
