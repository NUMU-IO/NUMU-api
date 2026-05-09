"""Add celery_dead_letters table (Phase 5.3).

Revision ID: celery_dead_letters_20260508
Revises: wishlist_items_20260508
Create Date: 2026-05-08

Tasks that exhausted their retry budget previously disappeared with
just a warning log. This table persists exhausted invocations so
operators can see "what's been failing this week", manually retry
from the hub, and audit failures during incident response.

Schema decisions:
  * tenant_id IS NULLABLE (unlike most tenant-scoped tables) because
    platform-wide tasks like backups have no tenant. Don't ALTER
    other DLQ-using tables to copy this pattern; use it only here.
  * `args` / `kwargs` JSONB so manual retry can reproduce the call
    with no schema changes when task signatures evolve.
  * Partial index on `WHERE status = 'pending'` keeps the hub's
    "needs attention" query cheap even when historical resolved
    rows accumulate.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB, UUID

from alembic import op

revision: str = "celery_dead_letters_20260508"
down_revision: str | None = "wishlist_items_20260508"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    dl_status = sa.Enum(
        "pending",
        "retried",
        "resolved",
        "abandoned",
        name="deadletterstatus",
        schema="public",
    )
    dl_status.create(op.get_bind(), checkfirst=True)

    op.create_table(
        "celery_dead_letters",
        sa.Column(
            "id",
            UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("tenant_id", UUID(as_uuid=True), nullable=True),
        sa.Column("store_id", UUID(as_uuid=True), nullable=True),
        sa.Column("task_name", sa.String(length=200), nullable=False),
        sa.Column("args", JSONB, nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column(
            "kwargs", JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")
        ),
        sa.Column("queue", sa.String(length=50), nullable=True),
        sa.Column("status", dl_status, nullable=False, server_default="pending"),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("first_failed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_failed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("retried_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("retried_by_user_id", UUID(as_uuid=True), nullable=True),
        sa.Column("retry_task_id", sa.String(length=64), nullable=True),
        sa.Column("operator_note", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            onupdate=sa.func.now(),
            nullable=False,
        ),
        schema="public",
    )

    op.create_index(
        "ix_celery_dead_letters_tenant",
        "celery_dead_letters",
        ["tenant_id"],
        unique=False,
        schema="public",
    )
    op.create_index(
        "ix_celery_dead_letters_task_name",
        "celery_dead_letters",
        ["task_name"],
        unique=False,
        schema="public",
    )
    op.create_index(
        "ix_celery_dead_letters_status",
        "celery_dead_letters",
        ["status"],
        unique=False,
        schema="public",
    )
    op.create_index(
        "ix_celery_dead_letters_pending",
        "celery_dead_letters",
        ["tenant_id", "task_name"],
        unique=False,
        schema="public",
        postgresql_where=sa.text("status = 'pending'"),
    )


def downgrade() -> None:
    op.drop_index(
        "ix_celery_dead_letters_pending",
        table_name="celery_dead_letters",
        schema="public",
    )
    op.drop_index(
        "ix_celery_dead_letters_status",
        table_name="celery_dead_letters",
        schema="public",
    )
    op.drop_index(
        "ix_celery_dead_letters_task_name",
        table_name="celery_dead_letters",
        schema="public",
    )
    op.drop_index(
        "ix_celery_dead_letters_tenant",
        table_name="celery_dead_letters",
        schema="public",
    )
    op.drop_table("celery_dead_letters", schema="public")
    sa.Enum(name="deadletterstatus", schema="public").drop(
        op.get_bind(), checkfirst=True
    )
