"""MCP tools: the whole ORDO contract, callable by an agent.

Every tool speaks the same language as the generic API: stable error
codes, dry-run on writes, and `requires_approval` metadata so the agent
knows what will demand a human before trying. Results are returned as
JSON text content; errors carry the kernel error payload.
"""

from __future__ import annotations

import uuid
from collections.abc import Awaitable, Callable
from typing import Any

from ordo_core import Environment
from ordo_core.actions import actions_for, dispatch
from ordo_core.explain import explain_record
from ordo_core.idempotency import remember, replay
from ordo_core.recordset import RecordSet
from ordo_core.reports import reports_available, run_report
from ordo_core.semantic import build_schema

ToolHandler = Callable[[Environment, dict[str, Any]], Awaitable[dict[str, Any]]]


async def _idempotent(
    env: Environment, key: str | None, payload: dict[str, Any], run: Callable[[], Awaitable[Any]]
) -> Any:
    """Same guarantee as the HTTP API; the key defaults to one per call."""
    effective = key or uuid.uuid4().hex
    cached = await replay(env.session, effective, payload)
    if cached is not None:
        return cached
    result = await run()
    await remember(env.session, effective, payload, result)
    await env.session.commit()
    return result


async def tool_schema(env: Environment, args: dict[str, Any]) -> dict[str, Any]:
    models = args.get("models")
    return build_schema(env.registry, models=models, compact=True)


async def tool_search(env: Environment, args: dict[str, Any]) -> dict[str, Any]:
    result: dict[str, Any] = await RecordSet(env, str(args["model"])).search(
        args.get("domain") or [],
        fields=args.get("fields"),
        limit=min(int(args.get("limit", 80)), 500),
        cursor=args.get("cursor"),
    )
    return result


async def tool_read(env: Environment, args: dict[str, Any]) -> dict[str, Any]:
    rows = await RecordSet(env, str(args["model"])).read(
        [int(record_id) for record_id in args["ids"]],
        fields=args.get("fields"),
    )
    return {"rows": rows}


async def tool_create(env: Environment, args: dict[str, Any]) -> dict[str, Any]:
    model = str(args["model"])
    values = args["values"]
    values_list = values if isinstance(values, list) else [values]
    records = RecordSet(env, model)
    if args.get("dry_run"):
        outcome: dict[str, Any] = await records.create(values_list, dry_run=True)
        return outcome

    async def run() -> dict[str, Any]:
        return {"ids": await records.create(values_list)}

    result: dict[str, Any] = await _idempotent(
        env,
        args.get("idempotency_key"),
        {"op": "create", "model": model, "values": values_list},
        run,
    )
    return result


async def tool_write(env: Environment, args: dict[str, Any]) -> dict[str, Any]:
    model = str(args["model"])
    record_id = int(args["id"])
    values = dict(args["values"])
    records = RecordSet(env, model)
    if args.get("dry_run"):
        outcome: Any = await records.write([record_id], values, dry_run=True)
        return dict(outcome) if isinstance(outcome, dict) else {"written": outcome}

    async def run() -> dict[str, Any]:
        return {"written": await records.write([record_id], values)}

    result: dict[str, Any] = await _idempotent(
        env,
        args.get("idempotency_key"),
        {"op": "write", "model": model, "id": record_id, "values": values},
        run,
    )
    return result


async def tool_list_actions(env: Environment, args: dict[str, Any]) -> dict[str, Any]:
    model = str(args["model"])
    return {"model": model, "actions": [spec.describe() for spec in actions_for(model)]}


async def tool_run_action(env: Environment, args: dict[str, Any]) -> dict[str, Any]:
    model = str(args["model"])
    record_id = int(args["id"])
    action_name = str(args["action"])
    params = dict(args.get("params") or {})
    if args.get("dry_run"):
        return await dispatch(env, model, action_name, record_id, params, dry_run=True)

    async def run() -> dict[str, Any]:
        return await dispatch(env, model, action_name, record_id, params)

    result: dict[str, Any] = await _idempotent(
        env,
        args.get("idempotency_key"),
        {"op": action_name, "model": model, "id": record_id, "params": params},
        run,
    )
    return result


async def tool_explain(env: Environment, args: dict[str, Any]) -> dict[str, Any]:
    return await explain_record(env, str(args["model"]), int(args["id"]))


async def tool_list_reports(env: Environment, args: dict[str, Any]) -> dict[str, Any]:
    return {"reports": [spec.describe() for spec in reports_available()]}


async def tool_run_report(env: Environment, args: dict[str, Any]) -> dict[str, Any]:
    return await run_report(env, str(args["name"]), dict(args.get("params") or {}))


def _obj(properties: dict[str, Any], required: list[str]) -> dict[str, Any]:
    return {"type": "object", "properties": properties, "required": required}


MODEL_PROP = {"type": "string", "description": "Nombre del modelo, ej. sale.order"}
DRY_RUN_PROP = {
    "type": "boolean",
    "description": "Simular: ejecuta y revierte todo, sin efectos",
}
KEY_PROP = {
    "type": "string",
    "description": "Idempotency-Key propia; por defecto se genera una por llamada",
}

TOOLS: list[dict[str, Any]] = [
    {
        "name": "ordo_schema",
        "description": (
            "Describe los modelos disponibles: campos con su significado en lenguaje "
            "llano y ejemplos. Empieza aquí para saber qué existe."
        ),
        "inputSchema": _obj({"models": {"type": "array", "items": {"type": "string"}}}, []),
    },
    {
        "name": "ordo_search",
        "description": (
            "Busca registros con un dominio ORDO (lista de tuplas campo/operador/valor)."
        ),
        "inputSchema": _obj(
            {
                "model": MODEL_PROP,
                "domain": {"type": "array", "description": 'Ej. [["state","=","draft"]]'},
                "fields": {"type": "array", "items": {"type": "string"}},
                "limit": {"type": "integer"},
                "cursor": {"type": "string"},
            },
            ["model"],
        ),
    },
    {
        "name": "ordo_read",
        "description": "Lee registros por id.",
        "inputSchema": _obj(
            {
                "model": MODEL_PROP,
                "ids": {"type": "array", "items": {"type": "integer"}},
                "fields": {"type": "array", "items": {"type": "string"}},
            },
            ["model", "ids"],
        ),
    },
    {
        "name": "ordo_create",
        "description": (
            "Crea registros. Los importes van como string decimal, nunca float. "
            "Usa dry_run primero si dudas."
        ),
        "inputSchema": _obj(
            {
                "model": MODEL_PROP,
                "values": {"description": "Objeto o lista de objetos con los campos"},
                "dry_run": DRY_RUN_PROP,
                "idempotency_key": KEY_PROP,
            },
            ["model", "values"],
        ),
    },
    {
        "name": "ordo_write",
        "description": "Modifica un registro existente.",
        "inputSchema": _obj(
            {
                "model": MODEL_PROP,
                "id": {"type": "integer"},
                "values": {"type": "object"},
                "dry_run": DRY_RUN_PROP,
                "idempotency_key": KEY_PROP,
            },
            ["model", "id", "values"],
        ),
    },
    {
        "name": "ordo_list_actions",
        "description": (
            "Lista las acciones de negocio de un modelo, con su resumen y si "
            "requieren aprobación humana."
        ),
        "inputSchema": _obj({"model": MODEL_PROP}, ["model"]),
    },
    {
        "name": "ordo_run_action",
        "description": (
            "Ejecuta una acción de negocio (confirmar, facturar, contabilizar...). "
            "Con dry_run simula sin quemar numeración legal."
        ),
        "inputSchema": _obj(
            {
                "model": MODEL_PROP,
                "id": {"type": "integer"},
                "action": {"type": "string", "description": "Ej. action_confirm"},
                "params": {"type": "object"},
                "dry_run": DRY_RUN_PROP,
                "idempotency_key": KEY_PROP,
                "approval_id": {
                    "type": "string",
                    "description": (
                        "Id de la aprobación humana ya resuelta, para operaciones "
                        "que la exigen; se consume una sola vez"
                    ),
                },
            },
            ["model", "id", "action"],
        ),
    },
    {
        "name": "ordo_explain",
        "description": (
            "Explica un registro: de dónde sale cada valor, qué acciones puede "
            "ejecutar ahora y cuáles están bloqueadas y por qué."
        ),
        "inputSchema": _obj({"model": MODEL_PROP, "id": {"type": "integer"}}, ["model", "id"]),
    },
    {
        "name": "ordo_list_reports",
        "description": "Lista los reportes disponibles con sus parámetros.",
        "inputSchema": _obj({}, []),
    },
    {
        "name": "ordo_run_report",
        "description": "Ejecuta un reporte de solo lectura, ej. account.trial_balance.",
        "inputSchema": _obj({"name": {"type": "string"}, "params": {"type": "object"}}, ["name"]),
    },
]


def tool_authz_target(name: str, arguments: dict[str, Any]) -> tuple[str, str]:
    """(modelo, operación) que el PDP evalúa para cada tool (ADR-016)."""
    model = str(arguments.get("model", "")) or "ir.model"
    if name in ("ordo_schema", "ordo_list_actions"):
        return ("ir.model" if name == "ordo_schema" else model, "read")
    if name in ("ordo_search", "ordo_read", "ordo_explain"):
        return (model, "read")
    if name == "ordo_create":
        return (model, "create")
    if name == "ordo_write":
        return (model, "write")
    if name == "ordo_run_action":
        return (model, str(arguments.get("action", "write")))
    if name in ("ordo_list_reports", "ordo_run_report"):
        return ("reports", "read")
    return ("ir.model", "read")


HANDLERS: dict[str, ToolHandler] = {
    "ordo_schema": tool_schema,
    "ordo_search": tool_search,
    "ordo_read": tool_read,
    "ordo_create": tool_create,
    "ordo_write": tool_write,
    "ordo_list_actions": tool_list_actions,
    "ordo_run_action": tool_run_action,
    "ordo_explain": tool_explain,
    "ordo_list_reports": tool_list_reports,
    "ordo_run_report": tool_run_report,
}
