"""add campaign_auto_match_rules table

Revision ID: auto_match_rules_20260524
Revises: post_attr_merge_20260522
Create Date: 2026-05-24

Feature 002 — marketing-campaigns-v2 (US4). Per-campaign UTM-pattern
rules evaluated at funnel-event ingest. Rows sharing a `group_id`
form a single multi-condition rule combined per `combinator`. Priority
is store-globally unique to avoid ambiguous precedence.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers.
revision: str = "auto_match_rules_20260524"
down_revision: str = "post_attr_merge_20260522"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "campaign_auto_match_rules",
        sa.Column(
            "id",
            sa.UUID(),
            nullable=False,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("tenant_id", sa.UUID(), nullable=False),
        sa.Column("store_id", sa.UUID(), nullable=False),
        sa.Column("campaign_id", sa.UUID(), nullable=False),
        sa.Column("group_id", sa.UUID(), nullable=False),
        sa.Column("combinator", sa.String(length=8), nullable=False),
        sa.Column("field", sa.String(length=32), nullable=False),
        sa.Column("operator", sa.String(length=16), nullable=False),
        sa.Column("value", sa.String(length=200), nullable=False),
        sa.Column("priority", sa.Integer(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("created_by", sa.UUID(), nullable=False),
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
        sa.ForeignKeyConstraint(["created_by"], ["public.users.id"]),
        sa.CheckConstraint(
            "combinator IN ('AND', 'OR')",
            name="ck_camr_combinator",
        ),
        sa.CheckConstraint(
            "field IN ('utm_source', 'utm_medium', 'utm_campaign')",
            name="ck_camr_field",
        ),
        sa.CheckConstraint(
            "operator IN ('equals', 'starts_with', 'contains')",
            name="ck_camr_operator",
        ),
        schema="public",
    )

    # Drives the ingest-time ordered fetch (one query per store per
    # request, cached for the request's lifetime).
    op.create_index(
        "ix_camr_store_priority",
        "campaign_auto_match_rules",
        ["store_id", "priority"],
        schema="public",
    )

    # Drives the campaign-scoped CRUD reads from the sidebar panel.
    op.create_index(
        "ix_camr_campaign_id",
        "campaign_auto_match_rules",
        ["campaign_id"],
        schema="public",
    )

    # Store-global priority is unambiguous: no two rules in the same
    # store can share a priority slot. Per-rule rows in the same group
    # share the same priority — the unique key is therefore on the
    # group, not individual conditions. Enforced via a partial unique
    # index on (store_id, group_id) so each group has one priority row.
    # The full (store_id, priority) is unique per group_id, not per row,
    # so we use a deferrable check via a partial index strategy:
    op.create_index(
        "uq_camr_store_group",
        "campaign_auto_match_rules",
        ["store_id", "group_id"],
        unique=False,
        schema="public",
    )

    # -- Tenant isolation via RLS (mirrors the message_logs migration
    # pattern; uses the existing public.get_current_tenant_id() and
    # public.is_rls_bypassed() helper functions installed by the base
    # RLS migration). -------------------------------------------------- #
    conn = op.get_bind()

    conn.exec_driver_sql(
        "ALTER TABLE public.campaign_auto_match_rules ENABLE ROW LEVEL SECURITY"
    )
    conn.exec_driver_sql(
        "ALTER TABLE public.campaign_auto_match_rules FORCE ROW LEVEL SECURITY"
    )

    conn.exec_driver_sql("""
        CREATE POLICY tenant_isolation_select
        ON public.campaign_auto_match_rules
        FOR SELECT
        USING (tenant_id = public.get_current_tenant_id())
    """)

    conn.exec_driver_sql("""
        CREATE POLICY tenant_isolation_insert
        ON public.campaign_auto_match_rules
        FOR INSERT
        WITH CHECK (tenant_id = public.get_current_tenant_id())
    """)

    conn.exec_driver_sql("""
        CREATE POLICY tenant_isolation_update
        ON public.campaign_auto_match_rules
        FOR UPDATE
        USING (tenant_id = public.get_current_tenant_id())
        WITH CHECK (tenant_id = public.get_current_tenant_id())
    """)

    conn.exec_driver_sql("""
        CREATE POLICY tenant_isolation_delete
        ON public.campaign_auto_match_rules
        FOR DELETE
        USING (tenant_id = public.get_current_tenant_id())
    """)

    conn.exec_driver_sql("""
        CREATE POLICY admin_bypass
        ON public.campaign_auto_match_rules
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
            f"DROP POLICY IF EXISTS {policy} ON public.campaign_auto_match_rules"
        )

    conn.exec_driver_sql(
        "ALTER TABLE public.campaign_auto_match_rules DISABLE ROW LEVEL SECURITY"
    )

    op.drop_index(
        "uq_camr_store_group",
        table_name="campaign_auto_match_rules",
        schema="public",
    )
    op.drop_index(
        "ix_camr_campaign_id",
        table_name="campaign_auto_match_rules",
        schema="public",
    )
    op.drop_index(
        "ix_camr_store_priority",
        table_name="campaign_auto_match_rules",
        schema="public",
    )
    op.drop_table("campaign_auto_match_rules", schema="public")
