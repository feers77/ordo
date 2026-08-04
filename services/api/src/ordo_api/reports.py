"""Report endpoints: read-only aggregations declared by the modules.

`GET /api/v1/reports` is discovery; `GET /api/v1/reports/{name}` runs one.
Query parameters travel as-is to the handler, which validates and coerces
them — the API layer does not guess types.
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Request
from ordo_core import Environment
from ordo_core.errors import KernelError
from ordo_core.reports import reports_available, run_report

from ordo_api.deps import get_env
from ordo_api.records import _wrap

router = APIRouter(prefix="/api/v1", tags=["reports"])


@router.get("/reports")
async def list_reports(env: Annotated[Environment, Depends(get_env)]) -> dict[str, Any]:
    return {"reports": [spec.describe() for spec in reports_available()]}


@router.get("/reports/{name}")
async def get_report_result(
    name: str,
    request: Request,
    env: Annotated[Environment, Depends(get_env)],
) -> dict[str, Any]:
    params = dict(request.query_params)
    try:
        return await run_report(env, name, params)
    except KernelError as exc:
        raise _wrap(exc) from exc
