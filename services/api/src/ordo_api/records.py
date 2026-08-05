"""Generic record API (design F2-04).

Every write requires `Idempotency-Key` and supports `?dry_run=true`
(AGENTS.md §6). Errors keep the standard payload shape (§5).
"""

from __future__ import annotations

import json
from typing import Annotated, Any, ClassVar

from fastapi import APIRouter, Depends, Header, Query
from ordo_core import Environment
from ordo_core.errors import KernelError
from ordo_core.idempotency import remember, replay
from ordo_core.recordset import RecordSet
from ordo_core.transactions import TransactionRunner
from ordo_runtime import OrdoError
from pydantic import BaseModel, Field

from ordo_api.deps import get_env

router = APIRouter(prefix="/api/v1", tags=["records"])


class KernelHTTPError(OrdoError):
    """Bridges kernel errors to the standard HTTP error payload."""

    STATUS: ClassVar[dict[str, int]] = {
        "MODEL_NOT_FOUND": 404,
        "RECORD_NOT_FOUND": 404,
        "ACTION_UNKNOWN": 404,
        "REPORT_UNKNOWN": 404,
        "REPORT_PARAM_REQUIRED": 400,
        "FIELD_UNKNOWN": 422,
        "FIELD_REQUIRED": 422,
        "FIELD_READONLY": 422,
        "FIELD_INVALID_VALUE": 422,
        "FIELD_NOT_STORED": 422,
        "AGGREGATE_INVALID_FIELD": 422,
        "AGGREGATE_UNKNOWN": 422,
        "AGGREGATE_INVALID_ORDER": 422,
        "CONCURRENT_MODIFICATION": 409,
        "IDEMPOTENCY_KEY_REUSED": 409,
        "INVALID_CURSOR": 400,
        "TENANT_INVALID": 400,
        "SANDBOX_NESTED": 409,
        "SANDBOX_SOURCE_NOT_FOUND": 404,
        "SANDBOX_REFUSED": 403,
        "SANDBOX_NAME_INVALID": 400,
        "NL_UNAVAILABLE": 503,
        "NL_TIMEOUT": 504,
        "NL_MODEL_FAILED": 502,
        "NL_INVALID_RESPONSE": 422,
        "NL_INVALID_DOMAIN": 422,
    }
    # Fallos de transporte: el mismo intento puede salir bien más tarde. El
    # resto de los códigos del kernel describen datos, y reintentarlos igual
    # vuelve a fallar (docs/api/errors.md).
    RETRYABLE: ClassVar[frozenset[str]] = frozenset({"NL_TIMEOUT", "NL_MODEL_FAILED"})

    def __init__(self, error: KernelError) -> None:
        super().__init__(
            error.message,
            code=error.code,
            status_code=self.STATUS.get(error.code, 422),
            hint=error.hint,
            retryable=error.code in self.RETRYABLE,
        )
        self.current_state = error.current_state

    def to_payload(self, trace_id: str | None = None) -> dict[str, Any]:
        payload = super().to_payload(trace_id)
        if self.current_state is not None:
            payload["error"]["current_state"] = self.current_state
        return payload


def _wrap(error: KernelError) -> KernelHTTPError:
    return KernelHTTPError(error)


async def _idempotent(env: Environment, key: str | None, payload: Any, run: Any) -> dict[str, Any]:
    if not key:
        raise OrdoError(
            "Falta el header Idempotency-Key.",
            code="IDEMPOTENCY_KEY_REQUIRED",
            status_code=400,
            hint="Toda escritura debe ser idempotente (AGENTS.md §6).",
        )
    try:
        cached = await replay(env.session, key, payload)
    except KernelError as exc:
        raise _wrap(exc) from exc
    if cached is not None:
        return cached
    try:
        response: dict[str, Any] = await run()
    except KernelError as exc:
        raise _wrap(exc) from exc
    await remember(env.session, key, payload, response)
    await env.session.commit()
    return response


class CreateRequest(BaseModel):
    values: list[dict[str, Any]] | dict[str, Any]


class WriteRequest(BaseModel):
    values: dict[str, Any]
    expected_version: int | None = None


class BatchRequest(BaseModel):
    op: str = Field(pattern="^(create|write|unlink)$")
    values: list[dict[str, Any]] | dict[str, Any] | None = None
    ids: list[int] | None = None
    expected_version: int | None = None


class TxRequest(BaseModel):
    atomic: bool = True
    operations: list[dict[str, Any]]


class AggregateRequest(BaseModel):
    domain: list[Any] = []
    group_by: list[str] = []
    aggregates: list[str] = ["count"]
    order: str | None = None
    limit: int = 80


# /tx se declara antes que /{model}: FastAPI resuelve por orden y
# "tx" seria capturado como nombre de modelo.
@router.post("/tx")
async def transaction(
    body: TxRequest,
    env: Annotated[Environment, Depends(get_env)],
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
    dry_run: bool = Query(default=False),
) -> dict[str, Any]:
    runner = TransactionRunner(env)

    async def run() -> dict[str, Any]:
        results = await runner.run(body.operations, atomic=body.atomic, dry_run=dry_run)
        return {"atomic": body.atomic, "results": results}

    if dry_run:
        try:
            return await run()
        except KernelError as exc:
            raise _wrap(exc) from exc
    return await _idempotent(
        env, idempotency_key, {"atomic": body.atomic, "operations": body.operations}, run
    )


# /{model}/aggregate se declara antes que /{model}/{record_id} por el mismo
# motivo que /tx: FastAPI resuelve por orden de declaración.
@router.post("/{model}/aggregate")
async def aggregate(
    model: str,
    body: AggregateRequest,
    env: Annotated[Environment, Depends(get_env)],
) -> dict[str, Any]:
    """Agrupa y agrega en la base. Solo lectura: sin Idempotency-Key."""
    try:
        return await RecordSet(env, model).read_group(
            body.domain,
            group_by=body.group_by,
            aggregates=body.aggregates,
            order=body.order,
            limit=body.limit,
        )
    except KernelError as exc:
        raise _wrap(exc) from exc


@router.get("/{model}")
async def search_read(
    model: str,
    env: Annotated[Environment, Depends(get_env)],
    domain: str | None = Query(default=None, description="Dominio JSON"),
    fields: str | None = Query(default=None, description="Campos separados por coma"),
    limit: int = Query(default=80, le=500),
    cursor: str | None = None,
    order: str | None = None,
) -> dict[str, Any]:
    try:
        parsed_domain = json.loads(domain) if domain else []
    except json.JSONDecodeError as exc:
        raise OrdoError(
            "El parámetro 'domain' no es JSON válido.",
            code="DOMAIN_MALFORMED",
            status_code=400,
        ) from exc
    try:
        return await RecordSet(env, model).search(
            parsed_domain,
            fields=fields.split(",") if fields else None,
            limit=limit,
            cursor=cursor,
        )
    except KernelError as exc:
        raise _wrap(exc) from exc


@router.get("/{model}/{record_id}")
async def read_one(
    model: str,
    record_id: int,
    env: Annotated[Environment, Depends(get_env)],
    fields: str | None = None,
) -> dict[str, Any]:
    try:
        rows = await RecordSet(env, model).read(
            [record_id], fields=fields.split(",") if fields else None
        )
    except KernelError as exc:
        raise _wrap(exc) from exc
    if not rows:
        raise OrdoError(
            f"No existe {model} con id {record_id}.",
            code="RECORD_NOT_FOUND",
            status_code=404,
        )
    return rows[0]


@router.post("/{model}", status_code=201)
async def create(
    model: str,
    body: CreateRequest,
    env: Annotated[Environment, Depends(get_env)],
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
    dry_run: bool = Query(default=False),
) -> dict[str, Any]:
    values = body.values if isinstance(body.values, list) else [body.values]
    records = RecordSet(env, model)

    async def run() -> dict[str, Any]:
        outcome = await records.create(values, dry_run=dry_run)
        return outcome if dry_run else {"ids": outcome}

    if dry_run:
        try:
            return await run()
        except KernelError as exc:
            raise _wrap(exc) from exc
    return await _idempotent(
        env, idempotency_key, {"model": model, "op": "create", "values": values}, run
    )


@router.patch("/{model}/{record_id}")
async def write(
    model: str,
    record_id: int,
    body: WriteRequest,
    env: Annotated[Environment, Depends(get_env)],
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
    dry_run: bool = Query(default=False),
) -> dict[str, Any]:
    records = RecordSet(env, model)

    async def run() -> dict[str, Any]:
        written = await records.write(
            [record_id],
            body.values,
            expected_version=body.expected_version,
            dry_run=dry_run,
        )
        return {"written": written}

    if dry_run:
        try:
            return await run()
        except KernelError as exc:
            raise _wrap(exc) from exc
    return await _idempotent(
        env,
        idempotency_key,
        {"model": model, "op": "write", "id": record_id, "values": body.values},
        run,
    )


@router.delete("/{model}/{record_id}")
async def unlink(
    model: str,
    record_id: int,
    env: Annotated[Environment, Depends(get_env)],
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
    dry_run: bool = Query(default=False),
) -> dict[str, Any]:
    records = RecordSet(env, model)

    async def run() -> dict[str, Any]:
        return {"deleted": await records.unlink([record_id], dry_run=dry_run)}

    if dry_run:
        try:
            return await run()
        except KernelError as exc:
            raise _wrap(exc) from exc
    return await _idempotent(
        env, idempotency_key, {"model": model, "op": "unlink", "id": record_id}, run
    )


@router.post("/{model}/batch")
async def batch(
    model: str,
    body: BatchRequest,
    env: Annotated[Environment, Depends(get_env)],
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
    dry_run: bool = Query(default=False),
) -> dict[str, Any]:
    operation = {
        "op": body.op,
        "model": model,
        "values": body.values,
        "ids": body.ids,
        "expected_version": body.expected_version,
    }
    runner = TransactionRunner(env)

    async def run() -> dict[str, Any]:
        results = await runner.run([operation], atomic=True, dry_run=dry_run)
        return {"results": results}

    if dry_run:
        try:
            return await run()
        except KernelError as exc:
            raise _wrap(exc) from exc
    return await _idempotent(env, idempotency_key, operation, run)
