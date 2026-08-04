"""Stable IAM error codes (public contract, AGENTS.md §5)."""

from __future__ import annotations

from ordo_runtime import OrdoError


class IamError(OrdoError):
    status_code = 422


class OwnerNotFoundError(IamError):
    code = "IAM_OWNER_NOT_FOUND"
    status_code = 404


class OwnerInactiveError(IamError):
    code = "IAM_OWNER_INACTIVE"
    status_code = 409


class TenantMismatchError(IamError):
    code = "IAM_TENANT_MISMATCH"
    status_code = 409


class EmailTakenError(IamError):
    code = "IAM_EMAIL_TAKEN"
    status_code = 409


class ClientIdTakenError(IamError):
    code = "IAM_CLIENT_ID_TAKEN"
    status_code = 409


class GrantNotFoundError(IamError):
    code = "IAM_GRANT_NOT_FOUND"
    status_code = 404


class PrincipalNotFoundError(IamError):
    code = "IAM_PRINCIPAL_NOT_FOUND"
    status_code = 404


class TokenInvalidError(IamError):
    code = "IAM_TOKEN_INVALID"
    status_code = 401


class TokenExpiredError(IamError):
    code = "IAM_TOKEN_EXPIRED"
    status_code = 401
    retryable = True


class UnknownIdentityError(IamError):
    code = "IAM_UNKNOWN_IDENTITY"
    status_code = 401


class PrincipalSuspendedError(IamError):
    code = "IAM_PRINCIPAL_SUSPENDED"
    status_code = 403


class AgentAuthFailedError(IamError):
    code = "IAM_AGENT_AUTH_FAILED"
    status_code = 401


class AgentSuspendedError(IamError):
    code = "IAM_AGENT_SUSPENDED"
    status_code = 403


class DelegationNotAllowedError(IamError):
    code = "IAM_DELEGATION_NOT_ALLOWED"
    status_code = 403


class NoCapabilitiesError(IamError):
    code = "IAM_NO_CAPABILITIES"
    status_code = 403


class UnsupportedGrantError(IamError):
    code = "IAM_UNSUPPORTED_GRANT"
    status_code = 400


class NotAgentOwnerError(IamError):
    code = "IAM_NOT_AGENT_OWNER"
    status_code = 403


class ApprovalNotFoundError(IamError):
    code = "IAM_APPROVAL_NOT_FOUND"
    status_code = 404


class ApprovalPendingError(IamError):
    code = "IAM_APPROVAL_PENDING"
    status_code = 409
    retryable = True


class ApprovalRejectedError(IamError):
    code = "IAM_APPROVAL_REJECTED"
    status_code = 403


class ApprovalExpiredError(IamError):
    code = "IAM_APPROVAL_EXPIRED"
    status_code = 410


class ApprovalConsumedError(IamError):
    code = "IAM_APPROVAL_CONSUMED"
    status_code = 409


class ApprovalMismatchError(IamError):
    code = "IAM_APPROVAL_MISMATCH"
    status_code = 409


class NotApproverError(IamError):
    code = "IAM_NOT_APPROVER"
    status_code = 403


class IdempotencyKeyRequiredError(IamError):
    code = "IAM_IDEMPOTENCY_KEY_REQUIRED"
    status_code = 400


# -- canales de notificación / HITL por Telegram (F1-07) ---------------------


class LinkCodeInvalidError(IamError):
    """Un solo código para inexistente, vencido y ya usado: no da información."""

    code = "IAM_LINK_CODE_INVALID"
    status_code = 400


class ChannelAlreadyLinkedError(IamError):
    code = "IAM_CHANNEL_ALREADY_LINKED"
    status_code = 409


class ChannelNotVerifiedError(IamError):
    code = "IAM_CHANNEL_NOT_VERIFIED"
    status_code = 403


class CallbackInvalidError(IamError):
    code = "IAM_CALLBACK_INVALID"
    status_code = 403


class WebhookUnauthorizedError(IamError):
    code = "IAM_WEBHOOK_UNAUTHORIZED"
    status_code = 403


class TelegramNotConfiguredError(IamError):
    code = "IAM_TELEGRAM_NOT_CONFIGURED"
    status_code = 503


class TelegramDeliveryError(IamError):
    code = "IAM_TELEGRAM_DELIVERY_FAILED"
    status_code = 502
    retryable = True
