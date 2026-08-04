"""TED: el timbre electrónico que va dentro de cada DTE.

El TED se firma con la clave privada del CAF, no con el certificado del
emisor. El SII especifica SHA1withRSA sobre los bytes del elemento `<DD>`
aplanado (sin espacios entre elementos): no requiere canonicalización XML,
por eso se firma de verdad aquí (ADR-014).
"""

from __future__ import annotations

import base64
from datetime import UTC, datetime
from decimal import Decimal

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from cryptography.hazmat.primitives.serialization import load_pem_private_key
from localizations.cl.einvoicing.caf import Caf
from modules.einvoicing.contracts import InvoiceData
from ordo_core.errors import KernelError


class TedError(KernelError):
    """Error al construir o firmar el timbre."""


def _xml_escape(value: str) -> str:
    return value.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def build_dd(invoice: InvoiceData, folio: int, caf: Caf, timestamp: str) -> str:
    """Arma el `<DD>` aplanado, en el orden exacto que exige el esquema."""
    first_item = invoice.lines[0].description if invoice.lines else ""
    total = invoice.taxes.total_included.quantize(Decimal("1"))
    return (
        "<DD>"
        f"<RE>{_xml_escape(invoice.issuer.tax_id)}</RE>"
        f"<TD>{invoice.document_type_code}</TD>"
        f"<F>{folio}</F>"
        f"<FE>{invoice.issue_date.isoformat()}</FE>"
        f"<RR>{_xml_escape(invoice.receiver.tax_id)}</RR>"
        f"<RSR>{_xml_escape(invoice.receiver.name[:40])}</RSR>"
        f"<MNT>{total}</MNT>"
        f"<IT1>{_xml_escape(first_item[:40])}</IT1>"
        f"{caf.caf_xml}"
        f"<TSTED>{timestamp}</TSTED>"
        "</DD>"
    )


def sign_dd(dd: str, caf: Caf) -> str:
    """Firma RSA-SHA1 del DD con la clave del CAF, en base64.

    SHA-1 no es elección nuestra: es el algoritmo que el formato del SII
    exige para el timbre (`algoritmo="SHA1withRSA"`).
    """
    try:
        key = load_pem_private_key(caf.private_key_pem.encode(), password=None)
    except ValueError as exc:
        raise TedError("CL_CAF_BAD_KEY", "La clave privada del CAF no se pudo leer") from exc
    if not isinstance(key, rsa.RSAPrivateKey):
        raise TedError("CL_CAF_BAD_KEY", "La clave del CAF no es RSA")
    signature = key.sign(
        dd.encode("iso-8859-1", errors="replace"),
        padding.PKCS1v15(),
        hashes.SHA1(),  # noqa: S303 — exigido por el formato TED del SII
    )
    return base64.b64encode(signature).decode()


def build_ted(invoice: InvoiceData, folio: int, caf: Caf, *, now: datetime | None = None) -> str:
    if not caf.covers(folio):
        raise TedError(
            "CL_FOLIO_OUT_OF_CAF",
            f"El folio {folio} no está dentro del rango del CAF ({caf.range_from}-{caf.range_to})",
            hint="Asigna el folio desde el rango cargado con este CAF.",
        )
    if caf.document_type != invoice.document_type_code:
        raise TedError(
            "CL_CAF_WRONG_TYPE",
            f"El CAF autoriza el tipo {caf.document_type}, no el {invoice.document_type_code}",
        )
    timestamp = (now or datetime.now(tz=UTC)).strftime("%Y-%m-%dT%H:%M:%S")
    dd = build_dd(invoice, folio, caf, timestamp)
    frmt = sign_dd(dd, caf)
    return f'<TED version="1.0">{dd}<FRMT algoritmo="SHA1withRSA">{frmt}</FRMT></TED>'
