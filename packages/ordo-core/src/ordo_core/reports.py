"""Report registry: read-only business reports exposed through the API.

Mirrors `ordo_core.actions`: a module ships `reports.py`, declares its
reports with the decorator, and the API serves them generically. Reports
never write; they exist so an agent can ask "how do the books look" without
reimplementing accounting aggregation on its side.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from ordo_core.errors import KernelError

if TYPE_CHECKING:
    from ordo_core.environment import Environment

ReportHandler = Callable[["Environment", dict[str, Any]], Awaitable[dict[str, Any]]]


@dataclass(frozen=True)
class ReportSpec:
    name: str
    handler: ReportHandler
    summary: str
    params: dict[str, str] = field(default_factory=dict)

    def describe(self) -> dict[str, Any]:
        return {"name": self.name, "summary": self.summary, "params": self.params}


_REPORTS: dict[str, ReportSpec] = {}


def report(
    name: str,
    *,
    summary: str,
    params: dict[str, str] | None = None,
) -> Callable[[ReportHandler], ReportHandler]:
    def register(handler: ReportHandler) -> ReportHandler:
        _REPORTS[name] = ReportSpec(
            name=name, handler=handler, summary=summary, params=dict(params or {})
        )
        return handler

    return register


def reports_available() -> list[ReportSpec]:
    return sorted(_REPORTS.values(), key=lambda spec: spec.name)


def get_report(name: str) -> ReportSpec:
    spec = _REPORTS.get(name)
    if spec is None:
        available = ", ".join(s.name for s in reports_available()) or "ninguno"
        raise KernelError(
            "REPORT_UNKNOWN",
            f"No existe el reporte '{name}'",
            hint=f"Reportes disponibles: {available}.",
        )
    return spec


async def run_report(env: Environment, name: str, params: dict[str, Any]) -> dict[str, Any]:
    return await get_report(name).handler(env, params)
