"""add campaign_activities table

Revision ID: campaign_activities_20260524
Revises: campaign_auto_match_rules_20260524
Create Date: 2026-05-24

Feature 002 — marketing-campaigns-v2 (US5). Audit log of
merchant-initiated campaign actions. v1 captures only
`backfill_attribution`; the `type` column is extensible so future
activity types fold into the same surface (right-sidebar Activities
panel + reconciliation dashboard).
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers.
revision: str = "campaign_activities_20260524"
down_revision: str = "auto_match_rules_20260524"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "campaign_activities",
        sa.Column(
            "id",
            sa.UUID(),
            nullable=False,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("tenant_id", sa.UUID(), nullable=False),
        sa.Column("store_id", sa.UUID(), nullable=False),
        sa.Column("campaign_id", sa.UUID(), nullable=False),
        sa.Column("type", sa.String(length=32), nullable=False),
        sa.Column(
            "status",
            sa.String(length=16),
            nullable=False,
            server_default="running",
        ),
        sa.Column(
            "payload",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
        ),
        sa.Column("affected_count", sa.Integer(), nullable=True),
        sa.Column("skipped_count", sa.Integer(), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column(
            "run_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("run_by", sa.UUID(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(
            ["tenant_id"], ["public.tenants.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(["store_id"], ["public.stores.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["campaign_id"],
            ["public.marketing_campaigns.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(["run_by"], ["public.users.id"]),
        sa.CheckConstraint(
            "type IN ('backfill_attribution')",
            name="ck_campaign_activities_type",
        ),
        sa.CheckConstraint(
            "status IN ('running', 'completed', 'failed')",
            name="ck_campaign_activities_status",
        ),
        schema="public",
    )

    # Drives the activities log list (most recent first per campaign).
    op.create_index(
        "ix_campaign_activities_campaign_run_at",
        "campaign_activities",
        ["campaign_id", sa.text("run_at DESC")],
        schema="public",
    )

    # Quick check for in-progress backfills (FR concurrency guard via
    # 409 on POST). Partial index keeps it tiny.
    op.create_index(
        "ix_campaign_activities_store_running",
        "campaign_activities",
        ["store_id", "campaign_id"],
        schema="public",
        postgresql_where=sa.text("status = 'running'"),
    )

    # -- RLS --
    conn = op.get_bind()

    conn.exec_driver_sql(
        "ALTER TABLE public.campaign_activities ENABLE ROW LEVEL SECURITY"
    )
    conn.exec_driver_sql(
        "ALTER TABLE public.campaign_activities FORCE ROW LEVEL SECURITY"
    )

    conn.exec_driver_sql("""
        CREATE POLICY tenant_isolation_select
        ON public.campaign_activities
        FOR SELECT
        USING (tenant_id = public.get_current_tenant_id())
    """)

    conn.exec_driver_sql("""
        CREATE POLICY tenant_isolation_insert
        ON public.campaign_activities
        FOR INSERT
        WITH CHECK (tenant_id = public.get_current_tenant_id())
    """)

    conn.exec_driver_sql("""
        CREATE POLICY tenant_isolation_update
        ON public.campaign_activities
        FOR UPDATE
        USING (tenant_id = public.get_current_tenant_id())
        WITH CHECK (tenant_id = public.get_current_tenant_id())
    """)

    conn.exec_driver_sql("""
        CREATE POLICY tenant_isolation_delete
        ON public.campaign_activities
        FOR DELETE
        USING (tenant_id = public.get_current_tenant_id())
    """)

    conn.exec_driver_sql("""
        CREATE POLICY admin_bypass
        ON public.campaign_activities
        FOR ALL
        USING (public.is_rls_bypassed())
        WITH CHECK (public.is_rls_bypassed())
    """)


def downgrade() -> None:
    conn = op.get_bind()

    for policy in (
        "admin_bypass",
        "tenant_isolation_delete",
        "tenant_isolation_update",
        "tenant_isolation_insert",
        "tenant_isolation_select",
    ):
        conn.exec_driver_sql(
            f"DROP POLICY IF EXISTS {policy} ON public.campaign_activities"
        )

    conn.exec_driver_sql(
        "ALTER TABLE public.campaign_activities DISABLE ROW LEVEL SECURITY"
    )

    op.drop_index(
        "ix_campaign_activities_store_running",
        table_name="campaign_activities",
        schema="public",
    )
    op.drop_index(
        "ix_campaign_activities_campaign_run_at",
        table_name="campaign_activities",
        schema="public",
    )
    op.drop_table("campaign_activities", schema="public")
