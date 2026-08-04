"""Action endpoints: business transitions over the generic API (F2-04 §actions).

`GET /api/v1/{model}/actions` is discovery: an agent asks what it can do
and learns which operations demand human approval before trying them.
`POST .../actions/{action}` executes with the same contract as any write:
Idempotency-Key mandatory, `?dry_run=true` simulates and rolls back.
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Header, Query
from ordo_core import Environment
from ordo_core.actions import actions_for, dispatch, get_action
from ordo_core.errors import KernelError
from ordo_core.services.outbox import Outbox
from pydantic import BaseModel

from ordo_api.deps import get_env
from ordo_api.records import _idempotent, _wrap

router = APIRouter(prefix="/api/v1", tags=["actions"])


class ActionRequest(BaseModel):
    params: dict[str, Any] = {}


@router.get("/{model}/actions")
async def list_actions(
    model: str,
    env: Annotated[Environment, Depends(get_env)],
) -> dict[str, Any]:
    if model not in env.registry:
        raise _wrap(KernelError("MODEL_NOT_FOUND", f"No existe el modelo '{model}'"))
    return {"model": model, "actions": [spec.describe() for spec in actions_for(model)]}


@router.post("/{model}/{record_id}/actions/{action_name}")
async def run_action(
    model: str,
    record_id: int,
    action_name: str,
    body: ActionRequest,
    env: Annotated[Environment, Depends(get_env)],
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
    dry_run: bool = Query(default=False),
) -> dict[str, Any]:
    try:
        spec = get_action(model, action_name)
    except KernelError as exc:
        raise _wrap(exc) from exc

    async def run() -> dict[str, Any]:
        result = await dispatch(env, model, action_name, record_id, body.params, dry_run=dry_run)
        if not dry_run:
            # El evento sale por el outbox en la misma transacción: si el
            # commit falla, el evento nunca existió (ADR-008).
            await Outbox(env.session).emit(
                event_type=f"{model}.{action_name}",
                subject=f"{model}/{record_id}",
                payload={"result": result, "params": body.params},
            )
        return {
            "action": action_name,
            "requires_approval": spec.requires_approval,
            "result": result,
        }

    if dry_run:
        try:
            return await run()
        except KernelError as exc:
            raise _wrap(exc) from exc
    return await _idempotent(
        env,
        idempotency_key,
        {"model": model, "op": action_name, "id": record_id, "params": body.params},
        run,
    )
