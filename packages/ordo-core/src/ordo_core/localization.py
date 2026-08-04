"""Framework declarativo de localizaciones fiscales (F7).

Un pack de país es un directorio con YAML: plan de cuentas, impuestos,
tipos de documento y reglas de validación de identificadores tributarios.
El código es común; lo que cambia por país son los datos.

Cada pack declara sus fuentes normativas y su estado de revisión. Un pack
sin fuente citada no se carga: los datos fiscales inventados producen
declaraciones incorrectas, y eso tiene consecuencias legales para quien
use el sistema.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from ordo_core.errors import KernelError

COUNTRY_RE = re.compile(r"^[a-z]{2}$")
REVIEW_STATES = frozenset({"draft", "reviewed", "certified"})


class LocalizationError(KernelError):
    """Error de un pack de localización."""


@dataclass(frozen=True)
class TaxDefinition:
    code: str
    name: str
    rate: str
    tax_type: str = "percent"
    price_include: bool = False
    is_withholding: bool = False
    applies_to: tuple[str, ...] = ()
    legal_reference: str = ""


@dataclass(frozen=True)
class DocumentType:
    code: str
    name: str
    kind: str
    electronic: bool = False
    legal_reference: str = ""


@dataclass(frozen=True)
class LocalizationPack:
    country: str
    name: str
    version: str
    currency: str
    review_state: str
    sources: tuple[str, ...]
    accounts: tuple[dict[str, Any], ...] = ()
    taxes: tuple[TaxDefinition, ...] = ()
    document_types: tuple[DocumentType, ...] = ()
    identifier: dict[str, Any] = field(default_factory=dict)
    notes: str = ""

    @property
    def needs_professional_review(self) -> bool:
        """Un pack en borrador no debe usarse para declarar impuestos."""
        return self.review_state == "draft"


def _require(data: dict[str, Any], key: str, where: str) -> Any:
    if key not in data or data[key] in (None, "", [], {}):
        raise LocalizationError(
            "LOCALIZATION_INCOMPLETE",
            f"{where}: falta '{key}'",
            hint="Los packs fiscales exigen todos los campos declarados.",
        )
    return data[key]


def load_pack(path: Path) -> LocalizationPack:
    """Carga y valida un pack de localización desde su directorio."""
    manifest_path = path / "manifest.yaml"
    if not manifest_path.exists():
        raise LocalizationError("LOCALIZATION_NOT_FOUND", f"No hay manifest.yaml en {path}")
    try:
        manifest = yaml.safe_load(manifest_path.read_text()) or {}
    except yaml.YAMLError as exc:
        raise LocalizationError("LOCALIZATION_INVALID", f"{manifest_path}: YAML inválido") from exc

    where = str(manifest_path)
    country = str(_require(manifest, "country", where)).lower()
    if not COUNTRY_RE.match(country):
        raise LocalizationError(
            "LOCALIZATION_INVALID",
            f"{where}: código de país inválido: {country!r}",
            hint="Usa ISO 3166-1 alfa-2 en minúsculas: cl, py.",
        )

    review_state = str(manifest.get("review_state", "draft"))
    if review_state not in REVIEW_STATES:
        raise LocalizationError(
            "LOCALIZATION_INVALID",
            f"{where}: review_state inválido: {review_state!r}",
            hint=f"Valores: {sorted(REVIEW_STATES)}",
        )

    sources = manifest.get("sources") or []
    if not sources:
        raise LocalizationError(
            "LOCALIZATION_NO_SOURCES",
            f"{where}: el pack no declara fuentes normativas",
            hint=(
                "Cada dato fiscal debe poder rastrearse a la norma que lo respalda. "
                "Un pack sin fuentes produce declaraciones que nadie puede verificar."
            ),
        )

    return LocalizationPack(
        country=country,
        name=str(_require(manifest, "name", where)),
        version=str(_require(manifest, "version", where)),
        currency=str(_require(manifest, "currency", where)),
        review_state=review_state,
        sources=tuple(str(s) for s in sources),
        accounts=tuple(_load_yaml_list(path / "coa.yaml", "accounts")),
        taxes=tuple(_load_taxes(path / "taxes.yaml")),
        document_types=tuple(_load_document_types(path / "document_types.yaml")),
        identifier=_load_yaml_dict(path / "partner_validation.yaml"),
        notes=str(manifest.get("notes", "")),
    )


def _load_yaml_list(path: Path, key: str) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    data = yaml.safe_load(path.read_text()) or {}
    return list(data.get(key, []))


def _load_yaml_dict(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    return dict(yaml.safe_load(path.read_text()) or {})


def _load_taxes(path: Path) -> list[TaxDefinition]:
    definitions = []
    for raw in _load_yaml_list(path, "taxes"):
        where = f"{path}:{raw.get('code', '?')}"
        definitions.append(
            TaxDefinition(
                code=str(_require(raw, "code", where)),
                name=str(_require(raw, "name", where)),
                rate=str(_require(raw, "rate", where)),
                tax_type=str(raw.get("tax_type", "percent")),
                price_include=bool(raw.get("price_include", False)),
                is_withholding=bool(raw.get("is_withholding", False)),
                applies_to=tuple(raw.get("applies_to", ())),
                legal_reference=str(raw.get("legal_reference", "")),
            )
        )
    return definitions


def _load_document_types(path: Path) -> list[DocumentType]:
    types = []
    for raw in _load_yaml_list(path, "document_types"):
        where = f"{path}:{raw.get('code', '?')}"
        types.append(
            DocumentType(
                code=str(_require(raw, "code", where)),
                name=str(_require(raw, "name", where)),
                kind=str(_require(raw, "kind", where)),
                electronic=bool(raw.get("electronic", False)),
                legal_reference=str(raw.get("legal_reference", "")),
            )
        )
    return types


def discover_packs(root: Path) -> dict[str, LocalizationPack]:
    packs: dict[str, LocalizationPack] = {}
    if not root.exists():
        return packs
    for directory in sorted(root.iterdir()):
        if directory.is_dir() and (directory / "manifest.yaml").exists():
            pack = load_pack(directory)
            packs[pack.country] = pack
    return packs
