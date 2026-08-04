"""DDL for the kernel's cross-cutting tables (F2-05).

Created per tenant schema. Alembic owns migrations for business models;
these are kernel infrastructure and are created idempotently at bootstrap.
"""

from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

STATEMENTS = [
    """
    CREATE TABLE IF NOT EXISTS ir_sequence (
        id serial PRIMARY KEY,
        code text NOT NULL UNIQUE,
        name text NOT NULL,
        prefix text NOT NULL DEFAULT '',
        suffix text NOT NULL DEFAULT '',
        padding integer NOT NULL DEFAULT 5,
        next_number bigint NOT NULL DEFAULT 1,
        step integer NOT NULL DEFAULT 1,
        implementation text NOT NULL DEFAULT 'standard'
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS ir_job (
        id bigserial PRIMARY KEY,
        name text NOT NULL,
        payload jsonb NOT NULL DEFAULT '{}'::jsonb,
        state text NOT NULL DEFAULT 'pending',
        priority integer NOT NULL DEFAULT 100,
        run_at timestamptz NOT NULL DEFAULT now(),
        attempts integer NOT NULL DEFAULT 0,
        max_attempts integer NOT NULL DEFAULT 5,
        last_error text,
        locked_by text,
        locked_at timestamptz,
        create_date timestamptz NOT NULL DEFAULT now()
    )
    """,
    "CREATE INDEX IF NOT EXISTS ix_ir_job_ready ON ir_job (state, run_at, priority)",
    """
    CREATE TABLE IF NOT EXISTS ir_cron (
        id serial PRIMARY KEY,
        name text NOT NULL UNIQUE,
        job_name text NOT NULL,
        payload jsonb NOT NULL DEFAULT '{}'::jsonb,
        interval_seconds integer NOT NULL,
        next_call timestamptz NOT NULL DEFAULT now(),
        active boolean NOT NULL DEFAULT true
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS ir_outbox (
        id bigserial PRIMARY KEY,
        event_type text NOT NULL,
        subject text NOT NULL,
        payload jsonb NOT NULL,
        create_date timestamptz NOT NULL DEFAULT now(),
        published_at timestamptz
    )
    """,
    "CREATE INDEX IF NOT EXISTS ix_ir_outbox_pending ON ir_outbox (id) WHERE published_at IS NULL",
    """
    CREATE TABLE IF NOT EXISTS mail_message (
        id bigserial PRIMARY KEY,
        model text NOT NULL,
        res_id integer NOT NULL,
        body text NOT NULL,
        message_type text NOT NULL DEFAULT 'comment',
        author_principal text,
        author_kind text NOT NULL,
        create_date timestamptz NOT NULL DEFAULT now()
    )
    """,
    "CREATE INDEX IF NOT EXISTS ix_mail_message_record ON mail_message (model, res_id, id)",
    """
    CREATE TABLE IF NOT EXISTS mail_follower (
        id bigserial PRIMARY KEY,
        model text NOT NULL,
        res_id integer NOT NULL,
        principal_id text NOT NULL,
        create_date timestamptz NOT NULL DEFAULT now(),
        UNIQUE (model, res_id, principal_id)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS mail_activity (
        id bigserial PRIMARY KEY,
        model text NOT NULL,
        res_id integer NOT NULL,
        summary text NOT NULL,
        assigned_to text NOT NULL,
        date_deadline date NOT NULL,
        done boolean NOT NULL DEFAULT false,
        create_date timestamptz NOT NULL DEFAULT now()
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS ir_attachment (
        id bigserial PRIMARY KEY,
        name text NOT NULL,
        model text,
        res_id integer,
        mimetype text NOT NULL,
        file_size bigint NOT NULL,
        checksum text NOT NULL,
        storage_key text NOT NULL,
        create_date timestamptz NOT NULL DEFAULT now()
    )
    """,
    "CREATE INDEX IF NOT EXISTS ix_ir_attachment_checksum ON ir_attachment (checksum)",
]


async def create_kernel_tables(session: AsyncSession) -> None:
    for statement in STATEMENTS:
        await session.execute(text(statement))
    # Idempotencia incluida aquí: los servicios corren como ordo_app, que no
    # tiene DDL; crearla lazy en el primer request fallaría en producción.
    from ordo_core.idempotency import create_table as _create_idempotency

    await _create_idempotency(session)
