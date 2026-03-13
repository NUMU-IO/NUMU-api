"""Add webhook_subscriptions and webhook_delivery_logs tables.

Revision ID: ee1234567890
Revises: f7a8b9c0d1e2
Create Date: 2026-03-13
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import ARRAY, JSONB

from alembic import op

revision: str = "ee1234567890"
down_revision: str | None = "f7a8b9c0d1e2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # ── webhook_subscriptions ─────────────────────────────────────────────────
    op.create_table(
        "webhook_subscriptions",
        sa.Column(
            "id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "tenant_id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            sa.ForeignKey("public.tenants.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "store_id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            sa.ForeignKey("public.stores.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("url", sa.String(2048), nullable=False),
        # text[] — no PG enum, so new event types never need a migration
        sa.Column("events", ARRAY(sa.Text), nullable=False, server_default="{}"),
        sa.Column("secret", sa.String(255), nullable=False),
        sa.Column("is_active", sa.Boolean, nullable=False, server_default="true"),
        sa.Column("description", sa.Text, nullable=True),
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
            nullable=False,
        ),
        schema="public",
    )
    op.create_index(
        "idx_webhook_subscriptions_store_id",
        "webhook_subscriptions",
        ["store_id"],
        schema="public",
    )
    op.create_index(
        "idx_webhook_subscriptions_tenant_active",
        "webhook_subscriptions",
        ["tenant_id", "is_active"],
        schema="public",
    )

    # ── webhook_delivery_logs ─────────────────────────────────────────────────
    op.create_table(
        "webhook_delivery_logs",
        sa.Column(
            "id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "tenant_id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            sa.ForeignKey("public.tenants.id", ondelete="CASCADE"),
            nullable=False,
        ),
        # SET NULL: logs survive subscription deletion for audit purposes
        sa.Column(
            "subscription_id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            sa.ForeignKey("public.webhook_subscriptions.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "store_id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            sa.ForeignKey("public.stores.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("event_type", sa.String(100), nullable=False),
        sa.Column(
            "event_id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            nullable=False,
        ),
        sa.Column("payload", JSONB, nullable=False, server_default="{}"),
        # String, not PG enum — validated at the application layer
        sa.Column("status", sa.String(20), nullable=False, server_default="pending"),
        sa.Column("attempt_count", sa.Integer, nullable=False, server_default="0"),
        sa.Column("next_attempt_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_attempt_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_status_code", sa.Integer, nullable=True),
        sa.Column("last_response_body", sa.Text, nullable=True),
        sa.Column("last_error", sa.Text, nullable=True),
        sa.Column("exhausted_at", sa.DateTime(timezone=True), nullable=True),
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
            nullable=False,
        ),
        schema="public",
    )
    op.create_index(
        "idx_webhook_delivery_logs_subscription_id",
        "webhook_delivery_logs",
        ["subscription_id"],
        schema="public",
    )
    op.create_index(
        "idx_webhook_delivery_logs_store_created",
        "webhook_delivery_logs",
        ["store_id", sa.text("created_at DESC")],
        schema="public",
    )
    # Critical index for the Celery retry poller (runs every 15 seconds)
    op.create_index(
        "idx_webhook_delivery_logs_status_next_attempt",
        "webhook_delivery_logs",
        ["status", "next_attempt_at"],
        schema="public",
    )


def downgrade() -> None:
    op.drop_index(
        "idx_webhook_delivery_logs_status_next_attempt",
        table_name="webhook_delivery_logs",
        schema="public",
    )
    op.drop_index(
        "idx_webhook_delivery_logs_store_created",
        table_name="webhook_delivery_logs",
        schema="public",
    )
    op.drop_index(
        "idx_webhook_delivery_logs_subscription_id",
        table_name="webhook_delivery_logs",
        schema="public",
    )
    op.drop_table("webhook_delivery_logs", schema="public")

    op.drop_index(
        "idx_webhook_subscriptions_tenant_active",
        table_name="webhook_subscriptions",
        schema="public",
    )
    op.drop_index(
        "idx_webhook_subscriptions_store_id",
        table_name="webhook_subscriptions",
        schema="public",
    )
    op.drop_table("webhook_subscriptions", schema="public")
