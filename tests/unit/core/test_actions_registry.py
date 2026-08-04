"""Registro de acciones: declaración, descubrimiento y errores estables."""

import pytest
from ordo_core.actions import action, actions_for, get_action
from ordo_core.errors import KernelError


@action("test.model", "action_ping", summary="Responde pong", params={"echo": "Texto a devolver"})
async def ping(env: object, record_id: int, params: dict) -> dict:  # type: ignore[type-arg]
    return {"pong": params.get("echo", "")}


@action("test.model", "action_arm", summary="Peligrosa", requires_approval=True)
async def arm(env: object, record_id: int, params: dict) -> dict:  # type: ignore[type-arg]
    return {}


class TestRegistry:
    def test_actions_are_listed_sorted_with_metadata(self) -> None:
        specs = actions_for("test.model")
        names = [spec.name for spec in specs]
        assert names == sorted(names)
        described = {spec.name: spec.describe() for spec in specs}
        assert described["action_arm"]["requires_approval"] is True
        assert described["action_ping"]["params"] == {"echo": "Texto a devolver"}

    def test_unknown_action_names_the_alternatives(self) -> None:
        with pytest.raises(KernelError) as excinfo:
            get_action("test.model", "action_missing")
        assert excinfo.value.code == "ACTION_UNKNOWN"
        assert "action_ping" in (excinfo.value.hint or "")

    def test_model_without_actions_is_empty_not_an_error(self) -> None:
        assert actions_for("model.sin.acciones") == []

    def test_reregistration_replaces(self) -> None:
        @action("test.model", "action_ping", summary="Otra")
        async def ping2(env: object, record_id: int, params: dict) -> dict:  # type: ignore[type-arg]
            return {"pong": "v2"}

        assert get_action("test.model", "action_ping").summary == "Otra"
