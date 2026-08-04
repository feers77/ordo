"""Human-in-the-loop approval requests (F1-06, PLAN §2.6).

The pending operation is stored serialized and sealed by hash: exactly
what was approved gets executed, not one byte more.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ordo_iam.audit import append_audit
from ordo_iam.errors import (
    ApprovalConsumedError,
    ApprovalExpiredError,
    ApprovalMismatchError,
    ApprovalNotFoundError,
    ApprovalPendingError,
    ApprovalRejectedError,
    NotApproverError,
)
from ordo_iam.models import Agent, ApprovalRequest, ApprovalStatus

DEFAULT_TTL = timedelta(hours=24)


def operation_hash(operation: dict[str, Any]) -> str:
    canonical = json.dumps(operation, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(canonical.encode()).hexdigest()


class ApprovalService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def create(
        self,
        *,
        tenant: str,
        agent_id: uuid.UUID,
        requested_by: uuid.UUID,
        operation: dict[str, Any],
        idempotency_key: str,
    ) -> tuple[ApprovalRequest, bool]:
        """Idempotent creation; returns (request, created)."""
        existing = await self.session.scalar(
            select(ApprovalRequest).where(
                ApprovalRequest.tenant == tenant,
                ApprovalRequest.idempotency_key == idempotency_key,
            )
        )
        if existing is not None:
            return existing, False
        request = ApprovalRequest(
            tenant=tenant,
            agent_id=agent_id,
            requested_by=requested_by,
            operation=operation,
            operation_hash=operation_hash(operation),
            idempotency_key=idempotency_key,
            expires_at=datetime.now(UTC) + DEFAULT_TTL,
        )
        self.session.add(request)
        await self.session.commit()
        await append_audit(
            self.session,
            tenant=tenant,
            event_type="approval_created",
            payload={"approval_id": str(request.id), "operation_hash": request.operation_hash},
            principal_id=agent_id,
        )
        return request, True

    async def get(self, approval_id: uuid.UUID) -> ApprovalRequest:
        request = await self.session.get(ApprovalRequest, approval_id, populate_existing=True)
        if request is None:
            raise ApprovalNotFoundError("Solicitud de aprobación no encontrada.")
        return request

    async def resolve(
        self,
        approval_id: uuid.UUID,
        *,
        approver_id: uuid.UUID,
        approve: bool,
        reason: str | None = None,
    ) -> ApprovalRequest:
        request = await self.get(approval_id)
        agent = await self.session.get(Agent, request.agent_id)
        assert agent is not None
        if agent.owner_user_id != approver_id:
            raise NotApproverError(
                "Solo el dueño del agente puede resolver esta solicitud.",
            )
        if request.status != ApprovalStatus.pending:
            raise ApprovalConsumedError(
                f"La solicitud ya fue resuelta ({request.status.value}).",
            )
        request.status = ApprovalStatus.approved if approve else ApprovalStatus.rejected
        request.approver_id = approver_id
        request.resolved_at = datetime.now(UTC)
        request.reason = reason
        await self.session.commit()
        await append_audit(
            self.session,
            tenant=request.tenant,
            event_type="approval_approved" if approve else "approval_rejected",
            payload={"approval_id": str(request.id)},
            principal_id=approver_id,
        )
        return request

    async def consume(
        self,
        approval_id: uuid.UUID,
        *,
        agent_id: uuid.UUID,
        operation: dict[str, Any],
    ) -> ApprovalRequest:
        request = await self.get(approval_id)
        if request.agent_id != agent_id:
            raise ApprovalNotFoundError("Solicitud de aprobación no encontrada.")
        if request.status == ApprovalStatus.consumed:
            raise ApprovalConsumedError(
                "La aprobación ya fue consumida; una aprobación ejecuta una sola vez.",
            )
        if request.status == ApprovalStatus.expired or datetime.now(UTC) >= request.expires_at:
            raise ApprovalExpiredError(
                "La aprobación expiró.",
                hint="Crea una nueva solicitud de aprobación.",
            )
        if request.status == ApprovalStatus.rejected:
            raise ApprovalRejectedError("La solicitud fue rechazada por el aprobador.")
        if request.status == ApprovalStatus.pending:
            raise ApprovalPendingError(
                "La solicitud sigue pendiente.",
                hint="Espera la resolución del aprobador y reintenta con la misma Idempotency-Key.",
            )
        if operation_hash(operation) != request.operation_hash:
            raise ApprovalMismatchError(
                "La operación no coincide byte a byte con lo aprobado.",
                hint="Solo puede ejecutarse exactamente la operación aprobada.",
            )
        request.status = ApprovalStatus.consumed
        await self.session.commit()
        await append_audit(
            self.session,
            tenant=request.tenant,
            event_type="approval_consumed",
            payload={"approval_id": str(request.id)},
            principal_id=agent_id,
        )
        return request
