"""Domain error hierarchy and the stable error payload (AGENTS.md §5)."""

from __future__ import annotations

from typing import Any


class OrdoError(Exception):
    """Base for all domain errors. `code` is a stable public contract."""

    code: str = "INTERNAL_ERROR"
    status_code: int = 500
    retryable: bool = False
    requires_approval: bool = False

    def __init__(
        self,
        message: str,
        *,
        code: str | None = None,
        status_code: int | None = None,
        model: str | None = None,
        record_id: int | str | None = None,
        field: str | None = None,
        hint: str | None = None,
        retryable: bool | None = None,
        requires_approval: bool | None = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        if code is not None:
            self.code = code
        if status_code is not None:
            self.status_code = status_code
        self.model = model
        self.record_id = record_id
        self.field = field
        self.hint = hint
        if retryable is not None:
            self.retryable = retryable
        if requires_approval is not None:
            self.requires_approval = requires_approval

    def to_payload(self, trace_id: str | None = None) -> dict[str, Any]:
        error: dict[str, Any] = {
            "code": self.code,
            "message": self.message,
            "retryable": self.retryable,
            "requires_approval": self.requires_approval,
            "docs_url": f"https://docs.ordo.dev.feres.cl/errors/{self.code}",
        }
        if self.model is not None:
            error["model"] = self.model
        if self.record_id is not None:
            error["record_id"] = self.record_id
        if self.field is not None:
            error["field"] = self.field
        if self.hint is not None:
            error["hint"] = self.hint
        if trace_id is not None:
            error["trace_id"] = trace_id
        return {"error": error}
