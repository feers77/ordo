"""Per-transaction record cache with dependency-aware invalidation (F2-03)."""

from __future__ import annotations

from typing import Any

from ordo_core.compute import DependencyGraph


class _Missing:
    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return "<MISS>"


class RecordCache:
    MISS = _Missing()

    def __init__(self, graph: DependencyGraph | None = None) -> None:
        self._data: dict[tuple[str, int, str], Any] = {}
        self._graph = graph

    def get(self, model: str, record_id: int, field: str) -> Any:
        return self._data.get((model, record_id, field), self.MISS)

    def set(self, model: str, record_id: int, field: str, value: Any) -> None:
        self._data[(model, record_id, field)] = value

    def invalidate(self, model: str, record_ids: list[int], fields: list[str]) -> None:
        """Drop the given fields and, per the graph, everything depending on them."""
        targets: set[tuple[str, str]] = {(model, name) for name in fields}
        if self._graph is not None:
            targets.update(self._graph.affected(model, fields))
        for target_model, target_field in targets:
            for record_id in record_ids:
                self._data.pop((target_model, record_id, target_field), None)

    def invalidate_all(self) -> None:
        self._data.clear()
