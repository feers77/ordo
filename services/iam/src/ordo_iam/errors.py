"""Stable IAM error codes (public contract, CLAUDE.md §5)."""

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
