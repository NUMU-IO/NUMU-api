"""Add flow_trigger_emission_log table (backend-020).

Idempotency-gated record of every ``flowTriggerReceive`` mutation sent
to Shopify on a merchant's behalf. Unique on
``(store_id, dedup_key, trigger_handle)`` per backend-020 FR-002.

Revision ID: flow_trigger_log_20260511
Revises: customer_trust_20260511
Create Date: 2026-05-11
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "flow_trigger_log_20260511"
down_revision: str | None = "customer_trust_20260511"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "flow_trigger_emission_log",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            nullable=False,
        ),
        sa.Column(
            "tenant_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("public.tenants.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column(
            "store_id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
            index=True,
        ),
        sa.Column("source_event_id", sa.String(64), nullable=False),
        sa.Column("trigger_handle", sa.String(64), nullable=False),
        sa.Column("dedup_key", sa.String(256), nullable=False),
        sa.Column(
            "status",
            sa.String(32),
            server_default=sa.text("'pending'"),
            nullable=False,
        ),
        sa.Column("attempted_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("succeeded_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("error_reason", sa.Text, nullable=True),
        sa.Column(
            "payload_snapshot",
            postgresql.JSONB,
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
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
        sa.UniqueConstraint(
            "store_id",
            "dedup_key",
            "trigger_handle",
            name="uq_flow_trigger_dedup",
        ),
        schema="public",
    )
    op.create_index(
        "ix_flow_trigger_status_attempted",
        "flow_trigger_emission_log",
        ["status", "attempted_at"],
        schema="public",
    )


def downgrade() -> None:
    op.drop_index(
        "ix_flow_trigger_status_attempted",
        table_name="flow_trigger_emission_log",
        schema="public",
    )
    op.drop_table("flow_trigger_emission_log", schema="public")
