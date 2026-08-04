"""Record explanation for agents (design F3-03 §1).

Answers, for a single record, the three questions an agent asks before it
acts: where every value came from, which actions it can run right now and
which ones are blocked and why. Nothing is written: each action is probed
with `dispatch(..., dry_run=True)`, which runs the real handler inside a
savepoint and rolls it back, no-gap sequence numbers included.
"""

from __future__ import annotations

import ast
from datetime import datetime
from typing import Any

from ordo_core.actions import actions_for, dispatch
from ordo_core.compute import declared_depends
from ordo_core.environment import Environment
from ordo_core.errors import KernelError
from ordo_core.fields import Field
from ordo_core.recordset import RecordSet
from ordo_core.registry import ModelDefinition
from ordo_core.services.chatter import Chatter

HISTORY_LIMIT = 20
MULTI_VALUED = frozenset({"one2many", "many2many"})
# Formato de una línea de tracking escrita por `Chatter.track_changes`.
TRACKING_SEPARATOR = ": "
TRACKING_ARROW = " → "


async def explain_record(env: Environment, model: str, record_id: int) -> dict[str, Any]:
    """Explain one record: value provenance, actions and tracked history."""
    definition = env.registry[model]
    values = await _read(env, definition, record_id)
    return {
        "model": model,
        "id": record_id,
        "fields": _explain_fields(definition, values),
        "actions": await _explain_actions(env, model, record_id),
        "history": await _explain_history(env, model, record_id),
    }


async def _read(env: Environment, definition: ModelDefinition, record_id: int) -> dict[str, Any]:
    """Read every stored column; non-stored fields have no value to report."""
    stored = [
        name
        for name, field in definition.fields.items()
        if field.store and field.field_type not in MULTI_VALUED
    ]
    rows = await RecordSet(env, definition.name).read([record_id], fields=stored)
    if not rows:
        raise KernelError(
            "RECORD_NOT_FOUND",
            f"No existe {definition.name} con id {record_id}",
            hint="Comprueba el id: puede haberse eliminado o pertenecer a otro tenant.",
        )
    return rows[0]


def _explain_fields(
    definition: ModelDefinition, values: dict[str, Any]
) -> dict[str, dict[str, Any]]:
    explained: dict[str, dict[str, Any]] = {}
    for name, field in sorted(definition.fields.items()):
        if not (field.store or field.compute or field.related):
            continue
        value = values.get(name)
        explained[name] = {
            "value": value,
            "origin": _origin(field, value),
            "compute": field.compute,
            "depends": _depends(definition, field),
            "related": field.related,
            "agent_hint": field.agent_hint,
        }
    return explained


def _origin(field: Field, value: Any) -> str:
    """Where the value comes from, in the order the kernel resolves it."""
    if field.related:
        return "related"
    if field.compute:
        return "computed"
    if not field.required and value == field.default:
        return "default"
    return "stored"


def _depends(definition: ModelDefinition, field: Field) -> list[str]:
    """Paths whose change recomputes the field, as the registry sees them."""
    if field.related:
        return [field.related]
    if not field.compute:
        return []
    return list(declared_depends(definition.compute_method(field.compute)) or ())


async def _explain_actions(
    env: Environment, model: str, record_id: int
) -> dict[str, list[dict[str, Any]]]:
    """Split the model's actions by what this record can run right now.

    An action that needs mandatory params fails the probe and lands in
    `blocked`: its reason names the missing parameter, which is exactly
    what the agent needs to call it for real.
    """
    available: list[dict[str, Any]] = []
    blocked: list[dict[str, Any]] = []
    for spec in actions_for(model):
        described = spec.describe()
        try:
            outcome = await dispatch(env, model, spec.name, record_id, {}, dry_run=True)
        except KernelError as exc:
            blocked.append({**described, "reason": _reason(exc.code, exc.message, exc.hint)})
            continue
        validations = outcome.get("validations") or []
        if not validations:
            available.append(described)
            continue
        first = validations[0]
        reason = _reason(first["code"], first["message"], first.get("hint"))
        blocked.append({**described, "reason": reason})
    return {"available": available, "blocked": blocked}


def _reason(code: str, message: str, hint: str | None) -> dict[str, Any]:
    return {"code": code, "message": message, "hint": hint}


async def _explain_history(env: Environment, model: str, record_id: int) -> list[dict[str, Any]]:
    """Tracked changes from the chatter; empty when the model has none.

    The read runs inside a savepoint: a tenant without chatter tables must
    not poison the transaction the caller is still using.
    """
    savepoint = await env.session.begin_nested()
    try:
        messages = await Chatter(env.session).thread(model, record_id, limit=HISTORY_LIMIT)
    except Exception:
        await savepoint.rollback()
        return []
    await savepoint.commit()

    history: list[dict[str, Any]] = []
    for message in messages:
        if message.get("message_type") != "tracking":
            continue
        history.extend(_tracked_changes(message))
    return history


def _tracked_changes(message: dict[str, Any]) -> list[dict[str, Any]]:
    """Undo the one-line-per-field format of `Chatter.track_changes`."""
    author = message.get("author_principal") or message.get("author_kind")
    moment = message.get("create_date")
    date = moment.isoformat() if isinstance(moment, datetime) else moment
    changes: list[dict[str, Any]] = []
    for line in str(message.get("body") or "").splitlines():
        name, separator, rest = line.partition(TRACKING_SEPARATOR)
        if not separator or TRACKING_ARROW not in rest:
            continue
        old, _, new = rest.partition(TRACKING_ARROW)
        changes.append(
            {
                "field": name,
                "old": _literal(old),
                "new": _literal(new),
                "author": author,
                "date": date,
            }
        )
    return changes


def _literal(rendered: str) -> Any:
    """Best-effort inverse of `repr()`; anything exotic stays as text."""
    raw = rendered.strip()
    try:
        return ast.literal_eval(raw)
    except (ValueError, SyntaxError):
        return raw
