"""Datos del QR que la representación gráfica del DE debe llevar.

El hash del QR se calcula con el CSC (código secreto del contribuyente,
entregado por el SIFEN) concatenado a los parámetros. El CSC es un secreto:
viene del vault, nunca de la base de datos.
"""

from __future__ import annotations

import hashlib
from decimal import Decimal
from urllib.parse import urlencode

QR_BASE_URL = "https://ekuatia.set.gov.py/consultas/qr"


def build_qr_params(
    *,
    cdc: str,
    issue_date_iso: str,
    receiver_ruc: str,
    total: Decimal,
    total_iva: Decimal,
    item_count: int,
    digest_value: str,
    csc_id: str,
    csc: str,
) -> str:
    """Devuelve la URL completa del QR con su cHashQR."""
    params = [
        ("nVersion", "150"),
        ("Id", cdc),
        ("dFeEmiDE", issue_date_iso.encode().hex()),
        ("dRucRec", receiver_ruc),
        ("dTotGralOpe", str(total)),
        ("dTotIVA", str(total_iva)),
        ("cItems", str(item_count)),
        ("DigestValue", digest_value.encode().hex()),
        ("IdCSC", csc_id),
    ]
    query = urlencode(params)
    hashable = query + csc
    signature = hashlib.sha256(hashable.encode()).hexdigest()
    return f"{QR_BASE_URL}?{query}&cHashQR={signature}"
