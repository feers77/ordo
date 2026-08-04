"""Principals: iam_principal, iam_user, iam_service_client, iam_agent, iam_capability_grant.

Revision ID: 0001
Revises:
Create Date: 2026-08-04
"""

from typing import Any

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None

principal_type = sa.Enum("user", "service_client", "agent", name="principal_type")
principal_status = sa.Enum("active", "suspended", "deleted", name="principal_status")
autonomy_level = sa.Enum("observer", "propose", "execute", "execute_approve", name="autonomy_level")


def _timestamps() -> list[sa.Column[Any]]:
    return [
        sa.Column[Any](
            "create_date",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column[Any](
            "write_date",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column[Any]("version", sa.Integer, server_default=sa.text("1"), nullable=False),
    ]


def upgrade() -> None:
    op.create_table(
        "iam_principal",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("type", principal_type, nullable=False),
        sa.Column("tenant", sa.String(64), nullable=False, index=True),
        sa.Column("display_name", sa.String(256), nullable=False),
        sa.Column("status", principal_status, nullable=False, server_default="active"),
        *_timestamps(),
    )
    op.create_table(
        "iam_user",
        sa.Column(
            "principal_id",
            UUID(as_uuid=True),
            sa.ForeignKey("iam_principal.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("tenant", sa.String(64), nullable=False),
        sa.Column("email", sa.String(320), nullable=False),
        sa.Column("idp_sub", sa.String(256), unique=True),
        sa.Column("mfa_enrolled", sa.Boolean, nullable=False, server_default=sa.text("false")),
        *_timestamps(),
    )
    op.create_index(
        "uq_iam_user_tenant_email",
        "iam_user",
        ["tenant", sa.text("lower(email)")],
        unique=True,
    )
    op.create_table(
        "iam_service_client",
        sa.Column(
            "principal_id",
            UUID(as_uuid=True),
            sa.ForeignKey("iam_principal.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("client_id", sa.String(128), nullable=False, unique=True),
        sa.Column("description", sa.Text),
        sa.Column(
            "allowed_scopes",
            ARRAY(sa.String(64)),
            nullable=False,
            server_default=sa.text("'{}'"),
        ),
        *_timestamps(),
    )
    op.create_table(
        "iam_agent",
        sa.Column(
            "principal_id",
            UUID(as_uuid=True),
            sa.ForeignKey("iam_principal.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column(
            "owner_user_id",
            UUID(as_uuid=True),
            sa.ForeignKey("iam_user.principal_id", ondelete="RESTRICT"),
            nullable=False,
            index=True,
        ),
        sa.Column("model", sa.String(128), nullable=False),
        sa.Column("model_version", sa.String(64)),
        sa.Column("autonomy_level", autonomy_level, nullable=False, server_default="observer"),
        sa.Column("budget", JSONB, nullable=False, server_default=sa.text("'{}'")),
        *_timestamps(),
    )
    op.create_table(
        "iam_capability_grant",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "agent_id",
            UUID(as_uuid=True),
            sa.ForeignKey("iam_agent.principal_id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column(
            "granted_by",
            UUID(as_uuid=True),
            sa.ForeignKey("iam_user.principal_id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("cap", JSONB, nullable=False),
        sa.Column(
            "valid_from",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("valid_until", sa.DateTime(timezone=True)),
        sa.Column("revoked_at", sa.DateTime(timezone=True)),
        *_timestamps(),
    )


def downgrade() -> None:
    op.drop_table("iam_capability_grant")
    op.drop_table("iam_agent")
    op.drop_table("iam_service_client")
    op.drop_index("uq_iam_user_tenant_email", table_name="iam_user")
    op.drop_table("iam_user")
    op.drop_table("iam_principal")
    for enum in (autonomy_level, principal_status, principal_type):
        enum.drop(op.get_bind(), checkfirst=True)
