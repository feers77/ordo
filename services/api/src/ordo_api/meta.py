"""Introspection endpoints for agents (PLAN §3.6)."""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query
from ordo_core import Registry
from ordo_core.errors import KernelError
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


@router.get("/models")
async def list_models(registry: Annotated[Registry, Depends(get_registry)]) -> dict[str, Any]:
    return {
        "models": [
            {"model": name, "description": registry[name].description}
            for name in registry.model_names
        ]
    }
