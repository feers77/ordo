"""Computed fields and the dependency graph (design F2-03).

Compute methods always run in batch: they receive every affected record at
once, so N+1 recomputation is impossible by construction.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, TypeVar

from ordo_core.errors import KernelError

F = TypeVar("F", bound=Callable[..., Any])

DEPENDS_ATTR = "_ordo_depends"

Node = tuple[str, str]  # (modelo, campo)


def depends(*paths: str) -> Callable[[F], F]:
    """Declare which field paths trigger a recomputation."""
    if not paths:
        msg = "@depends requiere al menos una ruta"
        raise ValueError(msg)

    def decorator(method: F) -> F:
        setattr(method, DEPENDS_ATTR, tuple(paths))
        return method

    return decorator


def declared_depends(method: Any) -> tuple[str, ...] | None:
    return getattr(method, DEPENDS_ATTR, None)


class DependencyGraph:
    """Edges point from a trigger field to the computed field it invalidates."""

    def __init__(self) -> None:
        self._edges: dict[Node, set[Node]] = {}

    def add_edge(self, trigger: Node, dependent: Node) -> None:
        self._edges.setdefault(trigger, set()).add(dependent)

    def affected(self, model: str, changed_fields: list[str]) -> list[Node]:
        """Computed fields to recompute, in topological order."""
        pending = [(model, name) for name in changed_fields]
        seen: set[Node] = set()
        order: list[Node] = []
        while pending:
            node = pending.pop(0)
            for dependent in sorted(self._edges.get(node, ())):
                if dependent in seen:
                    continue
                seen.add(dependent)
                order.append(dependent)
                pending.append(dependent)
        return _topological(order, self._edges)

    def dependents_of(self, model: str, field: str) -> set[Node]:
        return set(self._edges.get((model, field), ()))

    def validate_acyclic(self) -> None:
        state: dict[Node, int] = {}

        def visit(node: Node, path: tuple[Node, ...]) -> None:
            status = state.get(node, 0)
            if status == 1:
                cycle = " -> ".join(f"{m}.{f}" for m, f in (*path, node))
                raise KernelError(
                    "COMPUTE_DEPENDENCY_CYCLE",
                    f"Ciclo entre campos calculados: {cycle}",
                    hint="Un campo calculado no puede depender de sí mismo, ni en cadena.",
                )
            if status == 2:
                return
            state[node] = 1
            for dependent in sorted(self._edges.get(node, ())):
                visit(dependent, (*path, node))
            state[node] = 2

        for node in sorted(self._edges):
            visit(node, ())


def _topological(nodes: list[Node], edges: dict[Node, set[Node]]) -> list[Node]:
    """Order so that a field always comes before the fields depending on it."""
    node_set = set(nodes)
    result: list[Node] = []
    visited: set[Node] = set()

    def visit(node: Node) -> None:
        if node in visited:
            return
        visited.add(node)
        for dependent in sorted(edges.get(node, ())):
            if dependent in node_set:
                visit(dependent)
        result.append(node)

    for node in nodes:
        visit(node)
    return list(reversed(result))
