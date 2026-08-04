"""PDP (roles, ACL, record rules) y auditoría encadenada.

Revision ID: 0003
Revises: 0002
Create Date: 2026-08-04
"""

from typing import Any

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


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
        "iam_role",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("tenant", sa.String(64), nullable=False, index=True),
        sa.Column("name", sa.String(128), nullable=False),
        *_timestamps(),
    )
    op.create_index("uq_iam_role_tenant_name", "iam_role", ["tenant", "name"], unique=True)
    op.create_table(
        "iam_role_member",
        sa.Column(
            "role_id",
            UUID(as_uuid=True),
            sa.ForeignKey("iam_role.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column(
            "principal_id",
            UUID(as_uuid=True),
            sa.ForeignKey("iam_principal.id", ondelete="CASCADE"),
            primary_key=True,
            index=True,
        ),
    )
    op.create_table(
        "iam_acl",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "role_id",
            UUID(as_uuid=True),
            sa.ForeignKey("iam_role.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column("model", sa.String(128), nullable=False),
        sa.Column("perm_read", sa.Boolean, nullable=False, server_default=sa.text("false")),
        sa.Column("perm_write", sa.Boolean, nullable=False, server_default=sa.text("false")),
        sa.Column("perm_create", sa.Boolean, nullable=False, server_default=sa.text("false")),
        sa.Column("perm_unlink", sa.Boolean, nullable=False, server_default=sa.text("false")),
        *_timestamps(),
    )
    op.create_index("uq_iam_acl_role_model", "iam_acl", ["role_id", "model"], unique=True)
    op.create_table(
        "iam_record_rule",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("tenant", sa.String(64), nullable=False, index=True),
        sa.Column("model", sa.String(128), nullable=False, index=True),
        sa.Column("name", sa.String(256), nullable=False),
        sa.Column("domain", JSONB, nullable=False),
        sa.Column("ops", ARRAY(sa.String(16)), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("role_id", UUID(as_uuid=True), sa.ForeignKey("iam_role.id", ondelete="CASCADE")),
        *_timestamps(),
    )
    op.create_table(
        "iam_audit_log",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column("ts", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("tenant", sa.String(64), nullable=False, index=True),
        sa.Column("principal_id", UUID(as_uuid=True)),
        sa.Column("act_chain", JSONB, nullable=False, server_default=sa.text("'[]'")),
        sa.Column("event_type", sa.String(64), nullable=False),
        sa.Column("payload", JSONB, nullable=False, server_default=sa.text("'{}'")),
        sa.Column("trace_id", sa.String(64)),
        sa.Column("token_jti", sa.String(64)),
        sa.Column("prev_hash", sa.String(64), nullable=False),
        sa.Column("hash", sa.String(64), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("iam_audit_log")
    op.drop_table("iam_record_rule")
    op.drop_index("uq_iam_acl_role_model", table_name="iam_acl")
    op.drop_table("iam_acl")
    op.drop_table("iam_role_member")
    op.drop_index("uq_iam_role_tenant_name", table_name="iam_role")
    op.drop_table("iam_role")
