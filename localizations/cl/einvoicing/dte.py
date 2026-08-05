"""Construcción del XML de un DTE (formato del SII).

Los importes van en pesos enteros: el peso chileno no tiene decimales y el
SII rechaza montos con fracción. El redondeo ya lo hizo el motor de
impuestos; aquí solo se materializa.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

from modules.einvoicing.contracts import InvoiceData
from ordo_core.errors import KernelError

HUNDRED = Decimal("100")
CREDIT_DEBIT_NOTES = {"56", "61"}
# Boleta electrónica afecta y exenta. El esquema del SII les exige IndServicio
# en IdDoc, cosa que la factura no lleva.
BOLETAS = {"39", "41"}
# 3 = boleta de venta y servicios. Es el caso de una tienda; un local que solo
# presta servicios usa otro indicador y por eso el valor es configurable.
DEFAULT_IND_SERVICIO = "3"


class DteError(KernelError):
    """DTE imposible de construir con los datos entregados."""


def _peso(value: Decimal) -> str:
    return str(value.quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def _sub(parent: ET.Element, tag: str, text: str) -> ET.Element:
    node = ET.SubElement(parent, tag)
    node.text = text
    return node


def _line_amount(line: Any) -> Decimal:
    gross = line.price_unit * line.quantity
    if line.discount_percent:
        gross = gross * (HUNDRED - line.discount_percent) / HUNDRED
    return gross


def build_document(
    invoice: InvoiceData,
    folio: int,
    ted_xml: str,
    *,
    ind_servicio: str = DEFAULT_IND_SERVICIO,
) -> bytes:
    """Devuelve el `<DTE>` sin la firma de documento (esa la pone el Signer)."""
    if not invoice.lines:
        raise DteError("CL_DTE_EMPTY", "Un DTE sin líneas no es un documento")

    doc = ET.Element("Documento", {"ID": f"F{folio}T{invoice.document_type_code}"})

    header = ET.SubElement(doc, "Encabezado")
    id_doc = ET.SubElement(header, "IdDoc")
    _sub(id_doc, "TipoDTE", invoice.document_type_code)
    _sub(id_doc, "Folio", str(folio))
    _sub(id_doc, "FchEmis", invoice.issue_date.isoformat())
    if invoice.document_type_code in BOLETAS:
        _sub(id_doc, "IndServicio", ind_servicio)

    issuer = ET.SubElement(header, "Emisor")
    _sub(issuer, "RUTEmisor", invoice.issuer.tax_id)
    _sub(issuer, "RznSoc", invoice.issuer.name)
    if invoice.issuer.activity:
        _sub(issuer, "GiroEmis", invoice.issuer.activity)
    if invoice.issuer.address:
        _sub(issuer, "DirOrigen", invoice.issuer.address)
    if invoice.issuer.city:
        _sub(issuer, "CmnaOrigen", invoice.issuer.city)

    receiver = ET.SubElement(header, "Receptor")
    _sub(receiver, "RUTRecep", invoice.receiver.tax_id)
    _sub(receiver, "RznSocRecep", invoice.receiver.name)
    if invoice.receiver.activity:
        _sub(receiver, "GiroRecep", invoice.receiver.activity)
    if invoice.receiver.address:
        _sub(receiver, "DirRecep", invoice.receiver.address)
    if invoice.receiver.city:
        _sub(receiver, "CmnaRecep", invoice.receiver.city)

    exempt_total = sum((_line_amount(line) for line in invoice.lines if line.exempt), Decimal("0"))
    net_total = invoice.taxes.base - exempt_total
    iva = sum((t.amount for t in invoice.taxes.taxes if not t.is_withholding), Decimal("0"))

    totals = ET.SubElement(header, "Totales")
    if net_total > 0:
        _sub(totals, "MntNeto", _peso(net_total))
    if exempt_total > 0:
        _sub(totals, "MntExe", _peso(exempt_total))
    if iva > 0:
        _sub(totals, "TasaIVA", "19")
        _sub(totals, "IVA", _peso(iva))
    _sub(totals, "MntTotal", _peso(invoice.taxes.total_included))

    for index, line in enumerate(invoice.lines, start=1):
        detail = ET.SubElement(doc, "Detalle")
        _sub(detail, "NroLinDet", str(index))
        if line.exempt:
            _sub(detail, "IndExe", "1")
        _sub(detail, "NmbItem", line.description[:80])
        _sub(detail, "QtyItem", str(line.quantity))
        _sub(detail, "PrcItem", str(line.price_unit))
        if line.discount_percent:
            _sub(detail, "DescuentoPct", str(line.discount_percent))
        _sub(detail, "MontoItem", _peso(_line_amount(line)))

    if invoice.document_type_code in CREDIT_DEBIT_NOTES:
        if not invoice.reference_document:
            raise DteError(
                "CL_DTE_REFERENCE_REQUIRED",
                "Una nota de crédito o débito debe referenciar al documento que corrige",
                hint="Indica reference_document como 'tipo/folio', ej. '33/1042'.",
            )
        ref_type, _, ref_folio = invoice.reference_document.partition("/")
        reference = ET.SubElement(doc, "Referencia")
        _sub(reference, "NroLinRef", "1")
        _sub(reference, "TpoDocRef", ref_type)
        _sub(reference, "FolioRef", ref_folio or "0")
        _sub(reference, "RazonRef", invoice.reference_reason[:90])

    serialized = ET.tostring(doc, encoding="unicode")
    # El TED va dentro de Documento, antes del cierre. Se inserta como texto
    # porque ya viene firmado: re-parsearlo podría alterar los bytes firmados.
    serialized = serialized.replace("</Documento>", f"{ted_xml}</Documento>")
    return f'<DTE version="1.0">{serialized}</DTE>'.encode("iso-8859-1", errors="replace")
