"""Introspection endpoints for agents (PLAN §3.6)."""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query
from ordo_core import Registry
from ordo_core.actions import actions_for
from ordo_core.errors import KernelError
from ordo_core.reports import reports_available
from ordo_core.semantic import build_schema

from ordo_api.deps import get_registry
from ordo_api.records import _wrap

router = APIRouter(prefix="/meta/v1", tags=["meta"])


@router.get("/schema")
async def semantic_schema(
    registry: Annotated[Registry, Depends(get_registry)],
    models: str | None = Query(default=None, description="Modelos separados por coma"),
    schema_format: str = Query(default="llm", alias="format", pattern="^(llm|full)$"),
) -> dict[str, Any]:
    try:
        return build_schema(
            registry,
            models=models.split(",") if models else None,
            compact=schema_format == "llm",
        )
    except KernelError as exc:
        raise _wrap(exc) from exc


@router.get("/actions")
async def action_catalog(
    registry: Annotated[Registry, Depends(get_registry)],
    models: str | None = Query(default=None, description="Modelos separados por coma"),
) -> dict[str, Any]:
    """Global catalog of actions and reports, the index of what can be done.

    Without it an agent has to walk `GET /api/v1/{model}/actions` model by
    model to find out which operations exist.
    """
    requested = [name.strip() for name in models.split(",") if name.strip()] if models else []
    for name in requested:
        if name not in registry:
            raise _wrap(KernelError("MODEL_NOT_FOUND", f"El modelo '{name}' no está registrado"))
    catalog = [
        {"model": name, **spec.describe()}
        for name in (requested or registry.model_names)
        for spec in actions_for(name)
    ]
    catalog.sort(key=lambda entry: (entry["model"], entry["name"]))
    return {"actions": catalog, "reports": [spec.describe() for spec in reports_available()]}


@router.get("/models")
async def list_models(registry: Annotated[Registry, Depends(get_registry)]) -> dict[str, Any]:
    return {
        "models": [
            {"model": name, "description": registry[name].description}
            for name in registry.model_names
        ]
    }
