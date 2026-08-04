"""Semantic schema for agents (F2, PLAN §3.6).

Generated from the registry, never hand-written: if a business field lacks
`agent_hint` the registry already refused to build, so the schema an agent
reads is always complete.
"""

from __future__ import annotations

from typing import Any

from ordo_core.errors import KernelError
from ordo_core.registry import ModelDefinition, Registry

# Campos que el kernel gestiona y que el agente no necesita ver en el formato
# compacto. `company_id` NO está aquí a propósito: aunque lo gestione el kernel,
# a qué compañía pertenece un registro es información de negocio que el agente
# necesita para operar en un tenant multi-compañía.
AUDIT_FIELDS = frozenset({"create_uid", "create_date", "write_uid", "write_date", "version"})

OPERATORS = [
    "=",
    "!=",
    ">",
    ">=",
    "<",
    "<=",
    "in",
    "not in",
    "like",
    "ilike",
    "not like",
    "not ilike",
]


def describe_model(definition: ModelDefinition, *, compact: bool) -> dict[str, Any]:
    fields: dict[str, Any] = {}
    for name, field in sorted(definition.fields.items()):
        if compact and name in AUDIT_FIELDS:
            continue
        entry: dict[str, Any] = {
            "type": field.field_type,
            "hint": field.agent_hint,
        }
        if field.examples:
            entry["examples"] = field.examples
        if field.required:
            entry["required"] = True
        if field.readonly:
            entry["readonly"] = True
        if not field.store:
            entry["filterable"] = False
        comodel = getattr(field, "comodel", None)
        if comodel:
            entry["relates_to"] = comodel
        selection = getattr(field, "selection", None)
        if selection:
            entry["values"] = [value for value, _ in selection]
        if field.delegated_from:
            entry["delegated_from"] = field.delegated_from
        fields[name] = entry

    return {
        "model": definition.name,
        "description": definition.description,
        "fields": fields,
    }


def build_schema(
    registry: Registry, models: list[str] | None = None, *, compact: bool = True
) -> dict[str, Any]:
    names = models or registry.model_names
    for name in names:
        if name not in registry:
            raise KernelError("MODEL_NOT_FOUND", f"El modelo '{name}' no está registrado")
    return {
        "version": "v1",
        "operators": OPERATORS,
        "domain_syntax": (
            'Lista en notacion prefija: [("campo", "=", valor)], con "|" y "!" '
            "para OR y NOT; el AND es implicito. Las rutas punteadas navegan "
            "relaciones (partner_id.country_id.code)."
        ),
        "conventions": {
            "money": "Los importes son strings decimales, nunca float.",
            "timestamps": "UTC ISO-8601.",
            "writes": "Toda escritura acepta ?dry_run=true y exige Idempotency-Key.",
            "pagination": "Por cursor: usa next_cursor, no offset.",
        },
        "models": [describe_model(registry[name], compact=compact) for name in names],
    }
