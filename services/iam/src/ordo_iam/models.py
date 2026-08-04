"""SQLAlchemy models for IAM principals (design F1-01, ADR-003/004)."""

from __future__ import annotations

import enum
import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    String,
    Text,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    type_annotation_map = {  # noqa: RUF012
        dict[str, Any]: JSON().with_variant(JSONB(), "postgresql"),
        uuid.UUID: UUID(as_uuid=True),
    }


class PrincipalType(enum.StrEnum):
    user = "user"
    service_client = "service_client"
    agent = "agent"


class PrincipalStatus(enum.StrEnum):
    active = "active"
    suspended = "suspended"
    deleted = "deleted"


class AutonomyLevel(enum.StrEnum):
    observer = "observer"
    propose = "propose"
    execute = "execute"
    execute_approve = "execute_approve"


def _enum(e: type[enum.StrEnum], name: str) -> Enum:
    return Enum(e, name=name, values_callable=lambda x: [i.value for i in x])


class TimestampMixin:
    create_date: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    write_date: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )
    version: Mapped[int] = mapped_column(server_default=text("1"), nullable=False)


class Principal(TimestampMixin, Base):
    __tablename__ = "iam_principal"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    type: Mapped[PrincipalType] = mapped_column(_enum(PrincipalType, "principal_type"))
    tenant: Mapped[str] = mapped_column(String(64), index=True)
    display_name: Mapped[str] = mapped_column(String(256))
    status: Mapped[PrincipalStatus] = mapped_column(
        _enum(PrincipalStatus, "principal_status"),
        default=PrincipalStatus.active,
        server_default=PrincipalStatus.active.value,
    )


class User(TimestampMixin, Base):
    __tablename__ = "iam_user"

    principal_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("iam_principal.id", ondelete="CASCADE"), primary_key=True
    )
    tenant: Mapped[str] = mapped_column(String(64))
    email: Mapped[str] = mapped_column(String(320))
    idp_sub: Mapped[str | None] = mapped_column(String(256), unique=True)
    mfa_enrolled: Mapped[bool] = mapped_column(Boolean, default=False, server_default=text("false"))

    principal: Mapped[Principal] = relationship(lazy="joined")

    __table_args__ = (Index("uq_iam_user_tenant_email", "tenant", func.lower(email), unique=True),)


class ServiceClient(TimestampMixin, Base):
    __tablename__ = "iam_service_client"

    principal_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("iam_principal.id", ondelete="CASCADE"), primary_key=True
    )
    client_id: Mapped[str] = mapped_column(String(128), unique=True)
    description: Mapped[str | None] = mapped_column(Text)
    allowed_scopes: Mapped[list[str]] = mapped_column(
        ARRAY(String(64)), default=list, server_default=text("'{}'")
    )

    principal: Mapped[Principal] = relationship(lazy="joined")


class Agent(TimestampMixin, Base):
    __tablename__ = "iam_agent"

    principal_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("iam_principal.id", ondelete="CASCADE"), primary_key=True
    )
    owner_user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("iam_user.principal_id", ondelete="RESTRICT"), index=True
    )
    model: Mapped[str] = mapped_column(String(128))
    model_version: Mapped[str | None] = mapped_column(String(64))
    autonomy_level: Mapped[AutonomyLevel] = mapped_column(
        _enum(AutonomyLevel, "autonomy_level"),
        default=AutonomyLevel.observer,
        server_default=AutonomyLevel.observer.value,
    )
    budget: Mapped[dict[str, Any]] = mapped_column(default=dict, server_default=text("'{}'"))
    secret_hash: Mapped[str | None] = mapped_column(String(128))
    secret_salt: Mapped[str | None] = mapped_column(String(64))

    principal: Mapped[Principal] = relationship(lazy="joined")


class Role(TimestampMixin, Base):
    __tablename__ = "iam_role"
    __table_args__ = (Index("uq_iam_role_tenant_name", "tenant", "name", unique=True),)

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    tenant: Mapped[str] = mapped_column(String(64), index=True)
    name: Mapped[str] = mapped_column(String(128))


class RoleMember(Base):
    __tablename__ = "iam_role_member"

    role_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("iam_role.id", ondelete="CASCADE"), primary_key=True
    )
    principal_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("iam_principal.id", ondelete="CASCADE"), primary_key=True, index=True
    )


class Acl(TimestampMixin, Base):
    __tablename__ = "iam_acl"
    __table_args__ = (Index("uq_iam_acl_role_model", "role_id", "model", unique=True),)

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    role_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("iam_role.id", ondelete="CASCADE"), index=True
    )
    model: Mapped[str] = mapped_column(String(128))
    perm_read: Mapped[bool] = mapped_column(Boolean, default=False, server_default=text("false"))
    perm_write: Mapped[bool] = mapped_column(Boolean, default=False, server_default=text("false"))
    perm_create: Mapped[bool] = mapped_column(Boolean, default=False, server_default=text("false"))
    perm_unlink: Mapped[bool] = mapped_column(Boolean, default=False, server_default=text("false"))


class RecordRule(TimestampMixin, Base):
    __tablename__ = "iam_record_rule"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    tenant: Mapped[str] = mapped_column(String(64), index=True)
    model: Mapped[str] = mapped_column(String(128), index=True)
    name: Mapped[str] = mapped_column(String(256))
    domain: Mapped[dict[str, Any] | list[Any]] = mapped_column(
        JSON().with_variant(JSONB(), "postgresql")
    )
    ops: Mapped[list[str]] = mapped_column(
        ARRAY(String(16)), default=list, server_default=text("'{}'")
    )
    role_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("iam_role.id", ondelete="CASCADE"))


class AuditLog(Base):
    __tablename__ = "iam_audit_log"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    ts: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    tenant: Mapped[str] = mapped_column(String(64), index=True)
    principal_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    act_chain: Mapped[list[Any]] = mapped_column(
        JSON().with_variant(JSONB(), "postgresql"), default=list
    )
    event_type: Mapped[str] = mapped_column(String(64))
    payload: Mapped[dict[str, Any]] = mapped_column(default=dict)
    trace_id: Mapped[str | None] = mapped_column(String(64))
    token_jti: Mapped[str | None] = mapped_column(String(64))
    prev_hash: Mapped[str] = mapped_column(String(64))
    hash: Mapped[str] = mapped_column(String(64))


class CapabilityGrant(TimestampMixin, Base):
    __tablename__ = "iam_capability_grant"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    agent_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("iam_agent.principal_id", ondelete="CASCADE"), index=True
    )
    granted_by: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("iam_user.principal_id", ondelete="RESTRICT")
    )
    cap: Mapped[dict[str, Any]] = mapped_column()
    valid_from: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    valid_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
