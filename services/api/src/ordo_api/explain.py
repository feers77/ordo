"""Record explanation endpoint (design F3-03 §1).

Read-only by construction: the simulations behind `actions` run in a
savepoint and roll back, so explaining a record never changes it.
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends
from ordo_core import Environment
from ordo_core.errors import KernelError
from ordo_core.explain import explain_record

from ordo_api.deps import get_env
from ordo_api.records import _wrap

router = APIRouter(prefix="/api/v1", tags=["explain"])


@router.get("/{model}/{record_id}/explain")
async def explain(
    model: str,
    record_id: int,
    env: Annotated[Environment, Depends(get_env)],
) -> dict[str, Any]:
    """Explain one record: value provenance, doable actions and history."""
    try:
        return await explain_record(env, model, record_id)
    except KernelError as exc:
        raise _wrap(exc) from exc
