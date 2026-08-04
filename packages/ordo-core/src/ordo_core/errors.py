"""Kernel errors. Codes are part of the public contract (AGENTS.md §5)."""

from __future__ import annotations

from typing import Any


class KernelError(Exception):
    """Kernel-level failure with a stable error code."""

    def __init__(
        self,
        code: str,
        message: str,
        *,
        hint: str | None = None,
        current_state: list[dict[str, Any]] | None = None,
    ) -> None:
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message
        self.hint = hint
        # Estado actual del registro en conflictos de concurrencia: el agente
        # necesita verlo para reconciliar sin una lectura extra (PLAN §3.4).
        self.current_state = current_state
