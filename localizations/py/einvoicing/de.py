"""Construcción del XML del DE (documento electrónico del SIFEN).

El guaraní no tiene decimales en la práctica comercial: los importes van
enteros. El IVA paraguayo tiene dos tasas (10 % general, 5 % reducida) y el
XML exige subtotales separados por tasa; una tasa distinta es error, no un
caso más.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from decimal import ROUND_HALF_UP, Decimal

from modules.einvoicing.contracts import InvoiceData
from ordo_core.errors import KernelError

HUNDRED = Decimal("100")
KNOWN_RATES = {Decimal("10"), Decimal("5"), Decimal("0")}


class DeError(KernelError):
    """DE imposible de construir con los datos entregados."""


def _guarani(value: Decimal) -> str:
    return str(value.quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def _sub(parent: ET.Element, tag: str, text: str) -> ET.Element:
    node = ET.SubElement(parent, tag)
    node.text = text
    return node


def _split_ruc(tax_id: str) -> tuple[str, str]:
    number, _, dv = tax_id.partition("-")
    return number, dv


def build_de(
    invoice: InvoiceData,
    *,
    cdc: str,
    timbrado: str,
    establishment: str,
    expedition_point: str,
    document_number: int,
    emission_type: str,
    security_code: int,
    timbrado_start: str = "",
) -> bytes:
    """Arma el `rDE` completo, sin la firma (esa la pone el Signer)."""
    if not invoice.lines:
        raise DeError("PY_DE_EMPTY", "Un DE sin ítems no es un documento")

    amounts_by_rate: dict[Decimal, Decimal] = {}
    iva_by_rate: dict[Decimal, Decimal] = {}
    for tax_line in invoice.taxes.taxes:
        if tax_line.is_withholding:
            continue
        rate = (
            (tax_line.amount * HUNDRED / tax_line.base).quantize(Decimal("1"))
            if tax_line.base
            else Decimal("0")
        )
        if rate not in KNOWN_RATES:
            raise DeError(
                "PY_DE_UNKNOWN_RATE",
                f"El SIFEN solo conoce IVA 10 %, 5 % y exento; llegó {rate} %",
                hint="Revisa los impuestos aplicados a las líneas.",
            )
        amounts_by_rate[rate] = amounts_by_rate.get(rate, Decimal("0")) + tax_line.base
        iva_by_rate[rate] = iva_by_rate.get(rate, Decimal("0")) + tax_line.amount

    rde = ET.Element("rDE")
    _sub(rde, "dVerFor", "150")
    de = ET.SubElement(rde, "DE", {"Id": cdc})

    ope = ET.SubElement(de, "gOpeDE")
    _sub(ope, "iTipEmi", emission_type)
    _sub(ope, "dCodSeg", f"{security_code:09d}")

    timb = ET.SubElement(de, "gTimb")
    _sub(timb, "iTiDE", str(int(invoice.document_type_code)))
    _sub(timb, "dNumTim", timbrado)
    _sub(timb, "dEst", establishment)
    _sub(timb, "dPunExp", expedition_point)
    _sub(timb, "dNumDoc", f"{document_number:07d}")
    if timbrado_start:
        _sub(timb, "dFeIniT", timbrado_start)

    general = ET.SubElement(de, "gDatGralOpe")
    _sub(general, "dFeEmiDE", f"{invoice.issue_date.isoformat()}T00:00:00")

    commercial = ET.SubElement(general, "gOpeCom")
    _sub(commercial, "cMoneOpe", invoice.currency or "PYG")

    issuer_number, issuer_dv = _split_ruc(invoice.issuer.tax_id)
    issuer = ET.SubElement(general, "gEmis")
    _sub(issuer, "dRucEm", issuer_number)
    _sub(issuer, "dDVEmi", issuer_dv)
    _sub(issuer, "dNomEmi", invoice.issuer.name)
    if invoice.issuer.address:
        _sub(issuer, "dDirEmi", invoice.issuer.address)
    if invoice.issuer.activity:
        activity = ET.SubElement(issuer, "gActEco")
        _sub(activity, "dDesActEco", invoice.issuer.activity)

    receiver_number, receiver_dv = _split_ruc(invoice.receiver.tax_id)
    receiver = ET.SubElement(general, "gDatRec")
    _sub(receiver, "dRucRec", receiver_number)
    _sub(receiver, "dDVRec", receiver_dv)
    _sub(receiver, "dNomRec", invoice.receiver.name)

    detail = ET.SubElement(de, "gDtipDE")
    for line in invoice.lines:
        item = ET.SubElement(detail, "gCamItem")
        _sub(item, "dDesProSer", line.description[:120])
        _sub(item, "dCantProSer", str(line.quantity))
        value = ET.SubElement(item, "gValorItem")
        _sub(value, "dPUniProSer", str(line.price_unit))
        gross = line.price_unit * line.quantity
        if line.discount_percent:
            gross = gross * (HUNDRED - line.discount_percent) / HUNDRED
        _sub(value, "dTotBruOpeItem", _guarani(gross))

    totals = ET.SubElement(de, "gTotSub")
    zero = Decimal("0")
    _sub(totals, "dSubExe", _guarani(amounts_by_rate.get(zero, zero)))
    _sub(totals, "dSub5", _guarani(amounts_by_rate.get(Decimal("5"), zero)))
    _sub(totals, "dSub10", _guarani(amounts_by_rate.get(Decimal("10"), zero)))
    _sub(totals, "dTotOpe", _guarani(invoice.taxes.base))
    _sub(totals, "dIVA5", _guarani(iva_by_rate.get(Decimal("5"), zero)))
    _sub(totals, "dIVA10", _guarani(iva_by_rate.get(Decimal("10"), zero)))
    _sub(totals, "dTotIVA", _guarani(sum(iva_by_rate.values(), zero)))
    _sub(totals, "dTotGralOpe", _guarani(invoice.taxes.total_included))

    return ET.tostring(rde, encoding="unicode").encode("utf-8")
