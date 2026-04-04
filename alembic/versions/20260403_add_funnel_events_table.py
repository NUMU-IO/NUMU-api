"""add funnel_events table for conversion tracking

Revision ID: 5b8c3d9e0f1a
Revises: 4a7b2c8d9e0f
Create Date: 2026-04-03

"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB, UUID

from alembic import op

revision: str = "5b8c3d9e0f1a"
down_revision: str | None = "4a7b2c8d9e0f"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "funnel_events",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "tenant_id",
            UUID(as_uuid=True),
            sa.ForeignKey("public.tenants.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column(
            "store_id",
            UUID(as_uuid=True),
            sa.ForeignKey("public.stores.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column("session_fingerprint", sa.String(64), nullable=True, index=True),
        sa.Column("customer_id", UUID(as_uuid=True), nullable=True, index=True),
        sa.Column("step", sa.String(50), nullable=False),
        sa.Column("step_data", JSONB, nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        schema="public",
    )

    op.create_index(
        "ix_funnel_events_store_step_created",
        "funnel_events",
        ["store_id", "step", "created_at"],
        schema="public",
    )
    op.create_index(
        "ix_funnel_events_store_created",
        "funnel_events",
        ["store_id", "created_at"],
        schema="public",
    )

    # RLS policy for tenant isolation
    op.execute(
        """
        ALTER TABLE public.funnel_events ENABLE ROW LEVEL SECURITY;
        CREATE POLICY funnel_events_tenant_isolation ON public.funnel_events
            USING (tenant_id = current_setting('app.current_tenant_id', true)::uuid);
        """
    )


def downgrade() -> None:
    op.execute(
        "DROP POLICY IF EXISTS funnel_events_tenant_isolation ON public.funnel_events"
    )
    op.drop_index(
        "ix_funnel_events_store_created",
        table_name="funnel_events",
        schema="public",
    )
    op.drop_index(
        "ix_funnel_events_store_step_created",
        table_name="funnel_events",
        schema="public",
    )
    op.drop_table("funnel_events", schema="public")
