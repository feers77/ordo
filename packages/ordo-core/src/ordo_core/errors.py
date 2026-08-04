"""Kernel errors. Codes are part of the public contract (CLAUDE.md §5)."""

from __future__ import annotations


class KernelError(Exception):
    """Kernel-level failure with a stable error code."""

    def __init__(self, code: str, message: str, *, hint: str | None = None) -> None:
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message
        self.hint = hint
