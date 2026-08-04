"""Aprobaciones HITL: iam_approval_request.

Revision ID: 0004
Revises: 0003
Create Date: 2026-08-04
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None

approval_status = sa.Enum(
    "pending", "approved", "rejected", "expired", "consumed", name="approval_status"
)


def upgrade() -> None:
    op.create_table(
        "iam_approval_request",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("tenant", sa.String(64), nullable=False, index=True),
        sa.Column(
            "agent_id",
            UUID(as_uuid=True),
            sa.ForeignKey("iam_agent.principal_id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column(
            "requested_by",
            UUID(as_uuid=True),
            sa.ForeignKey("iam_user.principal_id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("operation", JSONB, nullable=False),
        sa.Column("operation_hash", sa.String(64), nullable=False),
        sa.Column("idempotency_key", sa.String(128), nullable=False),
        sa.Column("status", approval_status, nullable=False, server_default="pending"),
        sa.Column("approver_id", UUID(as_uuid=True)),
        sa.Column("resolved_at", sa.DateTime(timezone=True)),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("reason", sa.Text),
        sa.Column(
            "create_date",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "write_date",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("version", sa.Integer, server_default=sa.text("1"), nullable=False),
    )
    op.create_index(
        "uq_iam_approval_tenant_idem",
        "iam_approval_request",
        ["tenant", "idempotency_key"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index("uq_iam_approval_tenant_idem", table_name="iam_approval_request")
    op.drop_table("iam_approval_request")
    approval_status.drop(op.get_bind(), checkfirst=True)
