"""Declarative module security: default roles and their model permissions.

Each module ships a `security.yaml` declaring which roles touch its models
and how. The PDP stays deny-by-default: these specs are the explicit grants
that a tenant provisioning step loads into IAM. A model without an entry is
unreachable for everyone, which is the point.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import yaml

from ordo_core.errors import KernelError

VALID_PERMS = frozenset({"read", "write", "create", "unlink"})


class SecurityError(KernelError):
    """Invalid security declaration."""


@dataclass(frozen=True)
class RoleSpec:
    name: str
    # model -> permisos otorgados
    grants: dict[str, frozenset[str]] = field(default_factory=dict)


def _merge(target: dict[str, dict[str, set[str]]], source: dict[str, dict[str, set[str]]]) -> None:
    for role, models in source.items():
        bucket = target.setdefault(role, {})
        for model, perms in models.items():
            bucket.setdefault(model, set()).update(perms)


def _parse_file(path: Path) -> dict[str, dict[str, set[str]]]:
    try:
        raw = yaml.safe_load(path.read_text()) or {}
    except yaml.YAMLError as exc:
        raise SecurityError("SECURITY_INVALID_YAML", f"{path}: YAML inválido") from exc
    if not isinstance(raw, dict):
        raise SecurityError(
            "SECURITY_INVALID_SHAPE", f"{path}: se espera un mapa 'roles' en la raíz"
        )
    roles = raw.get("roles")
    if not isinstance(roles, dict):
        raise SecurityError(
            "SECURITY_INVALID_SHAPE", f"{path}: se espera un mapa 'roles' en la raíz"
        )
    out: dict[str, dict[str, set[str]]] = {}
    for role_name, models in roles.items():
        if not isinstance(models, dict):
            raise SecurityError(
                "SECURITY_INVALID_SHAPE", f"{path}: el rol '{role_name}' debe mapear modelos"
            )
        for model, perms in models.items():
            if not isinstance(perms, list):
                raise SecurityError(
                    "SECURITY_INVALID_SHAPE",
                    f"{path}: '{role_name}.{model}' debe ser una lista de permisos",
                )
            unknown = set(perms) - VALID_PERMS
            if unknown:
                raise SecurityError(
                    "SECURITY_INVALID_PERM",
                    f"{path}: permisos desconocidos en '{role_name}.{model}': {sorted(unknown)}",
                    hint=f"Permisos válidos: {sorted(VALID_PERMS)}.",
                )
            out.setdefault(str(role_name), {})[str(model)] = set(perms)
    return out


def load_security_specs(modules_root: Path) -> list[RoleSpec]:
    """Merges every module's security.yaml into per-role specs."""
    merged: dict[str, dict[str, set[str]]] = {}
    for path in sorted(modules_root.glob("*/security.yaml")):
        _merge(merged, _parse_file(path))
    return [
        RoleSpec(
            name=role,
            grants={model: frozenset(perms) for model, perms in sorted(models.items())},
        )
        for role, models in sorted(merged.items())
    ]
