"""Action registry: business transitions exposed as first-class operations.

State transitions are explicit methods, never raw writes to `state`
(AGENTS.md §4). This registry is what makes them reachable from the API:
a module declares its actions with the decorator and the API service
dispatches them generically, with dry-run and approval metadata included.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from ordo_core.errors import KernelError

if TYPE_CHECKING:
    from ordo_core.environment import Environment

Handler = Callable[["Environment", int, dict[str, Any]], Awaitable[dict[str, Any]]]


@dataclass(frozen=True)
class ActionSpec:
    """One business action on a model.

    `requires_approval` is metadata for the PDP: high-impact operations
    (posting, invoicing) must be able to demand a human in the loop
    (AGENTS.md §6). The API reports it; the PDP enforces it.
    """

    model: str
    name: str
    handler: Handler
    summary: str
    requires_approval: bool = False
    params: dict[str, str] = field(default_factory=dict)

    def describe(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "summary": self.summary,
            "requires_approval": self.requires_approval,
            "params": self.params,
        }


_ACTIONS: dict[tuple[str, str], ActionSpec] = {}


def action(
    model: str,
    name: str,
    *,
    summary: str,
    requires_approval: bool = False,
    params: dict[str, str] | None = None,
) -> Callable[[Handler], Handler]:
    """Registers a handler. Re-registration replaces (module reloads in tests)."""

    def register(handler: Handler) -> Handler:
        _ACTIONS[(model, name)] = ActionSpec(
            model=model,
            name=name,
            handler=handler,
            summary=summary,
            requires_approval=requires_approval,
            params=dict(params or {}),
        )
        return handler

    return register


def actions_for(model: str) -> list[ActionSpec]:
    return sorted(
        (spec for (m, _), spec in _ACTIONS.items() if m == model),
        key=lambda spec: spec.name,
    )


def get_action(model: str, name: str) -> ActionSpec:
    spec = _ACTIONS.get((model, name))
    if spec is None:
        available = ", ".join(s.name for s in actions_for(model)) or "ninguna"
        raise KernelError(
            "ACTION_UNKNOWN",
            f"El modelo '{model}' no tiene la acción '{name}'",
            hint=f"Acciones disponibles: {available}.",
        )
    return spec


async def dispatch(
    env: Environment,
    model: str,
    name: str,
    record_id: int,
    params: dict[str, Any] | None = None,
    *,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Runs the action; with dry_run the work happens and then rolls back.

    The rollback covers everything the handler touched, including no-gap
    sequence rows: a simulated confirmation never burns a legal number.
    """
    spec = get_action(model, name)
    if dry_run:
        savepoint = await env.session.begin_nested()
        try:
            result = await spec.handler(env, record_id, params or {})
            validations: list[dict[str, Any]] = []
        except KernelError as exc:
            result = {}
            validations = [{"code": exc.code, "message": exc.message}]
        finally:
            await savepoint.rollback()
        return {"would_return": result, "validations": validations}
    return await spec.handler(env, record_id, params or {})
