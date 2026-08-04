"""Validación de identificadores tributarios por país.

Estos son algoritmos públicos y verificables —módulo 11 con distintos
factores—, no interpretación normativa: se pueden implementar y probar con
casos conocidos. Los datos que sí requieren criterio profesional (tasas,
planes de cuentas) viven en los packs YAML, no aquí.
"""

from __future__ import annotations

import re

from ordo_core.errors import KernelError


class TaxIdError(KernelError):
    """Identificador tributario inválido."""


def _clean(value: str) -> str:
    return re.sub(r"[.\s]", "", value or "").upper()


# --- Chile: RUT ------------------------------------------------------------
# Dígito verificador por módulo 11 con factores cíclicos 2..7, de derecha a
# izquierda. El resultado 11 se representa como 0 y el 10 como K.

RUT_RE = re.compile(r"^(\d{1,8})-?([\dK])$")


def rut_check_digit(number: int) -> str:
    total = 0
    factor = 2
    for digit in reversed(str(number)):
        total += int(digit) * factor
        factor = 2 if factor == 7 else factor + 1
    remainder = 11 - (total % 11)
    if remainder == 11:
        return "0"
    if remainder == 10:
        return "K"
    return str(remainder)


def validate_rut(value: str) -> str:
    """Valida un RUT chileno y lo devuelve normalizado como 12345678-5."""
    cleaned = _clean(value)
    match = RUT_RE.match(cleaned)
    if not match:
        raise TaxIdError(
            "TAXID_INVALID_FORMAT",
            f"'{value}' no tiene formato de RUT",
            hint="Formato esperado: 12.345.678-5 o 12345678-5.",
        )
    number, given = match.groups()
    expected = rut_check_digit(int(number))
    if given != expected:
        raise TaxIdError(
            "TAXID_INVALID_CHECK_DIGIT",
            f"El dígito verificador de '{value}' debería ser {expected}, no {given}",
            hint="Revisa si hay un dígito transpuesto en el número.",
        )
    return f"{int(number)}-{expected}"


def format_rut(value: str) -> str:
    """Devuelve el RUT con puntos: 12.345.678-5."""
    normalized = validate_rut(value)
    number, check = normalized.split("-")
    return f"{int(number):,}".replace(",", ".") + f"-{check}"


# --- Paraguay: RUC ---------------------------------------------------------
# Dígito verificador por módulo 11 con factores crecientes desde 2, de derecha
# a izquierda. Resto 0 y 1 producen dígito 0.

RUC_RE = re.compile(r"^(\d{1,8})-?(\d)$")


def ruc_check_digit(number: int) -> str:
    total = 0
    factor = 2
    for digit in reversed(str(number)):
        total += int(digit) * factor
        factor += 1
    remainder = total % 11
    if remainder > 1:
        return str(11 - remainder)
    return "0"


def validate_ruc(value: str) -> str:
    """Valida un RUC paraguayo y lo devuelve normalizado como 80012345-6."""
    cleaned = _clean(value)
    match = RUC_RE.match(cleaned)
    if not match:
        raise TaxIdError(
            "TAXID_INVALID_FORMAT",
            f"'{value}' no tiene formato de RUC",
            hint="Formato esperado: 80012345-6.",
        )
    number, given = match.groups()
    expected = ruc_check_digit(int(number))
    if given != expected:
        raise TaxIdError(
            "TAXID_INVALID_CHECK_DIGIT",
            f"El dígito verificador de '{value}' debería ser {expected}, no {given}",
        )
    return f"{int(number)}-{expected}"


VALIDATORS = {
    "cl": validate_rut,
    "py": validate_ruc,
}


def validate(country: str, value: str) -> str:
    """Valida el identificador según el país; sin validador declarado, pasa."""
    validator = VALIDATORS.get(country.lower())
    if validator is None:
        return value.strip()
    return validator(value)
