"""Agentic test harness (T0.7).

Runs natural-language business tasks against a clean tenant through an
agent and measures outcome quality. In F0 there is no business logic:
only the harness skeleton, the task catalog format and its loader exist.
F3 provides the real ``AgentClient`` implementation (MCP-backed).

Task file format (``tests/agent/tasks/*.yaml``)::

    id: sales-001
    goal: "Crea una cotización para Acme por 10 unidades de SKU-100..."
    setup: [partner:acme, product:SKU-100@1000CLP]
    assert:
      - model: sale.order
        domain: [["partner_id.name", "=", "Acme"], ["state", "=", "sale"]]
        count: 1
      - expression: "order.amount_untaxed == 9500"
      - invariant: no_invalid_states
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

REQUIRED_TASK_KEYS = {"id", "goal", "setup", "assert"}


@dataclass(frozen=True)
class TaskSpec:
    """A business task an agent must complete, with verifiable outcome."""

    id: str
    goal: str
    setup: list[str]
    assertions: list[dict[str, Any]]
    source: Path

    @classmethod
    def from_file(cls, path: Path) -> TaskSpec:
        raw = yaml.safe_load(path.read_text())
        if not isinstance(raw, dict):
            msg = f"{path}: el YAML debe ser un mapa"
            raise ValueError(msg)
        missing = REQUIRED_TASK_KEYS - raw.keys()
        if missing:
            msg = f"{path}: faltan claves {sorted(missing)}"
            raise ValueError(msg)
        return cls(
            id=str(raw["id"]),
            goal=str(raw["goal"]),
            setup=list(raw["setup"]),
            assertions=list(raw["assert"]),
            source=path,
        )


@dataclass
class AgentRunResult:
    """Metrics for one task attempt — the product KPI (PLAN-MAESTRO §9)."""

    task_id: str
    completed: bool = False
    calls: int = 0
    latency_ms: float = 0.0
    invalid_states: list[str] = field(default_factory=list)
    pdp_blocks_correct: int = 0
    pdp_blocks_incorrect: int = 0
    failure_reason: str | None = None


class AgentClient(ABC):
    """Interface the F3 MCP-backed agent implementation must satisfy."""

    @abstractmethod
    async def run_task(self, task: TaskSpec) -> AgentRunResult:
        """Provision clean tenant, authenticate agent, pursue the goal."""


class AgentTaskRunner:
    """Loads the catalog and drives an ``AgentClient`` through it."""

    def __init__(self, client: AgentClient | None = None) -> None:
        self.client = client

    async def run_all(self, tasks: list[TaskSpec]) -> list[AgentRunResult]:
        if self.client is None:
            msg = "No hay AgentClient: la implementación MCP llega en F3"
            raise NotImplementedError(msg)
        return [await self.client.run_task(task) for task in tasks]


def load_tasks(tasks_dir: Path) -> list[TaskSpec]:
    """Load every ``*.yaml`` task in the catalog (``.example`` files excluded)."""
    return [
        TaskSpec.from_file(path)
        for path in sorted(tasks_dir.glob("*.yaml"))
        if not path.name.endswith(".example.yaml")
    ]
