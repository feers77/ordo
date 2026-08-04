"""CDC: el código de control de 44 dígitos que identifica cada DE del SIFEN.

La composición es fija: tipo de DE (2), RUC del emisor (8) y su DV (1),
establecimiento (3), punto de expedición (3), número de documento (7), tipo
de contribuyente (1), fecha de emisión AAAAMMDD (8), tipo de emisión (1),
código de seguridad (9) y el dígito verificador del conjunto (1). El DV usa
el mismo módulo 11 base 11 del RUC paraguayo.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from ordo_core.errors import KernelError

CDC_LENGTH = 44


class CdcError(KernelError):
    """CDC imposible de construir o inválido."""


def check_digit(digits: str) -> str:
    """Módulo 11 con factores 2..11 de derecha a izquierda, como el RUC."""
    total = 0
    factor = 2
    for digit in reversed(digits):
        total += int(digit) * factor
        factor = 2 if factor == 11 else factor + 1
    remainder = total % 11
    return str(11 - remainder) if remainder > 1 else "0"


@dataclass(frozen=True)
class CdcParts:
    document_type: str  # iTiDE, 2 dígitos
    issuer_ruc: str  # sin DV, hasta 8 dígitos
    issuer_ruc_dv: str
    establishment: str  # 3 dígitos
    expedition_point: str  # 3 dígitos
    document_number: int  # hasta 7 dígitos
    taxpayer_type: str  # 1=física, 2=jurídica
    issue_date: date
    emission_type: str  # 1=normal, 2=contingencia
    security_code: int  # 9 dígitos, aleatorio por documento


def build_cdc(parts: CdcParts) -> str:
    if not parts.issuer_ruc.isdigit() or len(parts.issuer_ruc) > 8:
        raise CdcError("PY_CDC_BAD_RUC", "El RUC del CDC debe ser numérico de hasta 8 dígitos")
    if not 0 < parts.document_number < 10_000_000:
        raise CdcError("PY_CDC_BAD_NUMBER", "El número de documento debe tener hasta 7 dígitos")
    if not 0 <= parts.security_code < 1_000_000_000:
        raise CdcError("PY_CDC_BAD_SECURITY_CODE", "El código de seguridad debe tener 9 dígitos")
    body = (
        f"{int(parts.document_type):02d}"
        f"{parts.issuer_ruc:0>8}"
        f"{parts.issuer_ruc_dv}"
        f"{parts.establishment:0>3}"
        f"{parts.expedition_point:0>3}"
        f"{parts.document_number:07d}"
        f"{parts.taxpayer_type}"
        f"{parts.issue_date.strftime('%Y%m%d')}"
        f"{parts.emission_type}"
        f"{parts.security_code:09d}"
    )
    if len(body) != CDC_LENGTH - 1:
        raise CdcError("PY_CDC_BAD_LENGTH", f"CDC malformado: {len(body) + 1} dígitos")
    return body + check_digit(body)


def validate_cdc(cdc: str) -> str:
    if len(cdc) != CDC_LENGTH or not cdc.isdigit():
        raise CdcError(
            "PY_CDC_BAD_LENGTH",
            f"Un CDC tiene {CDC_LENGTH} dígitos numéricos, no {len(cdc)}",
        )
    expected = check_digit(cdc[:-1])
    if cdc[-1] != expected:
        raise CdcError(
            "PY_CDC_BAD_CHECK_DIGIT",
            f"El dígito verificador del CDC debería ser {expected}, no {cdc[-1]}",
        )
    return cdc
