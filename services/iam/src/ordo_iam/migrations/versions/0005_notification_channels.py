"""Canales de notificación, códigos de vinculación y cola de jobs de IAM.

Revision ID: 0005
Revises: 0004
Create Date: 2026-08-04
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None

channel_type = sa.Enum("telegram", name="channel_type")


def upgrade() -> None:
    op.create_table(
        "iam_notification_channel",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("tenant", sa.String(64), nullable=False, index=True),
        sa.Column(
            "principal_id",
            UUID(as_uuid=True),
            sa.ForeignKey("iam_principal.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("channel_type", channel_type, nullable=False),
        sa.Column("address", sa.String(128), nullable=False),
        sa.Column("verified_at", sa.DateTime(timezone=True)),
        sa.Column("active", sa.Boolean, nullable=False, server_default=sa.text("true")),
        sa.Column(
            "create_date", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "write_date", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("version", sa.Integer, server_default=sa.text("1"), nullable=False),
    )
    # Una dirección activa pertenece a un solo principal: sin esto, dos cuentas
    # podrían quedar escuchando el mismo chat.
    op.create_index(
        "uq_iam_channel_address_active",
        "iam_notification_channel",
        ["channel_type", "address"],
        unique=True,
        postgresql_where=sa.text("active"),
    )
    op.create_index(
        "ix_iam_channel_principal",
        "iam_notification_channel",
        ["principal_id", "channel_type"],
    )

    op.create_table(
        "iam_channel_link_code",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("tenant", sa.String(64), nullable=False),
        sa.Column(
            "principal_id",
            UUID(as_uuid=True),
            sa.ForeignKey("iam_principal.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column("channel_type", channel_type, nullable=False),
        sa.Column("code_hash", sa.String(64), nullable=False, unique=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("used_at", sa.DateTime(timezone=True)),
        sa.Column(
            "create_date", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    )

    # Misma forma que la tabla del kernel (ADR-007): el aviso se encola dentro
    # de la transacción que crea la aprobación y sale del request.
    op.create_table(
        "ir_job",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column("name", sa.Text, nullable=False),
        sa.Column("payload", JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("state", sa.Text, nullable=False, server_default="pending"),
        sa.Column("priority", sa.Integer, nullable=False, server_default=sa.text("100")),
        sa.Column(
            "run_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column("attempts", sa.Integer, nullable=False, server_default=sa.text("0")),
        sa.Column("max_attempts", sa.Integer, nullable=False, server_default=sa.text("5")),
        sa.Column("last_error", sa.Text),
        sa.Column("locked_by", sa.Text),
        sa.Column("locked_at", sa.DateTime(timezone=True)),
        sa.Column(
            "create_date", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    )
    op.create_index("ix_ir_job_ready", "ir_job", ["state", "run_at", "priority"])


def downgrade() -> None:
    op.drop_index("ix_ir_job_ready", table_name="ir_job")
    op.drop_table("ir_job")
    op.drop_table("iam_channel_link_code")
    op.drop_index("ix_iam_channel_principal", table_name="iam_notification_channel")
    op.drop_index("uq_iam_channel_address_active", table_name="iam_notification_channel")
    op.drop_table("iam_notification_channel")
    channel_type.drop(op.get_bind(), checkfirst=True)
