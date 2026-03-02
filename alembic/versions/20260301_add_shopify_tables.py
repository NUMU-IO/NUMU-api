"""Add Shopify tables: installations, risk_assessments, payment_transactions,
automation_rules, automation_logs, shopify_app_settings.

Revision ID: d5e6f7a8b9c0
Revises: c4d5e6f7a8b9
Create Date: 2026-03-01
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

from alembic import op

revision: str = "d5e6f7a8b9c0"
down_revision: str | None = "c4d5e6f7a8b9"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # --- shopify_installations ---
    op.create_table(
        "shopify_installations",
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
            index=True,
        ),
        sa.Column(
            "store_id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            nullable=False,
            index=True,
        ),
        sa.Column(
            "shopify_domain", sa.String(255), unique=True, nullable=False, index=True
        ),
        sa.Column("access_token_encrypted", sa.String(512), nullable=False),
        sa.Column("scopes", sa.dialects.postgresql.ARRAY(sa.String), nullable=True),
        sa.Column("app_plan", sa.String(50), nullable=False, server_default="free"),
        sa.Column(
            "installed_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("uninstalled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default="true"),
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

    # --- risk_assessments ---
    op.create_table(
        "risk_assessments",
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
            index=True,
        ),
        sa.Column(
            "store_id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            nullable=False,
            index=True,
        ),
        sa.Column(
            "order_id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            nullable=True,
            index=True,
        ),
        sa.Column("shopify_order_id", sa.String(255), nullable=True),
        sa.Column("order_number", sa.String(50), nullable=True),
        sa.Column("customer_name", sa.String(255), nullable=True),
        sa.Column("customer_email", sa.String(255), nullable=True),
        sa.Column("total_cents", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("currency", sa.String(3), nullable=False, server_default="EGP"),
        sa.Column("payment_method", sa.String(50), nullable=True),
        sa.Column("risk_score", sa.Integer(), nullable=False),
        sa.Column("risk_level", sa.String(20), nullable=False),
        sa.Column("suggested_action", sa.String(50), nullable=True),
        sa.Column("action_taken", sa.String(50), nullable=True),
        sa.Column("action_taken_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("action_taken_by", sa.String(50), nullable=True),
        sa.Column("factors", JSONB(), nullable=False, server_default="[]"),
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
        "idx_risk_store_score",
        "risk_assessments",
        ["store_id", sa.text("risk_score DESC")],
        schema="public",
    )

    # --- payment_transactions ---
    op.create_table(
        "payment_transactions",
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
            index=True,
        ),
        sa.Column(
            "store_id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            nullable=False,
            index=True,
        ),
        sa.Column("order_id", sa.dialects.postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("channel", sa.String(50), nullable=False),
        sa.Column("gateway", sa.String(50), nullable=False),
        sa.Column("display_name", sa.String(100), nullable=True),
        sa.Column("amount_cents", sa.Integer(), nullable=False),
        sa.Column("currency", sa.String(3), nullable=False, server_default="EGP"),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("failure_reason", sa.String(255), nullable=True),
        sa.Column("failure_code", sa.String(50), nullable=True),
        sa.Column("gateway_transaction_id", sa.String(255), nullable=True),
        sa.Column("processing_started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("processing_completed_at", sa.DateTime(timezone=True), nullable=True),
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
        "idx_pt_store_channel",
        "payment_transactions",
        ["store_id", "channel", "created_at"],
        schema="public",
    )

    # --- automation_rules ---
    op.create_table(
        "automation_rules",
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
            index=True,
        ),
        sa.Column(
            "store_id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            nullable=False,
            index=True,
        ),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("priority", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("trigger_event", sa.String(50), nullable=False),
        sa.Column("conditions", JSONB(), nullable=False, server_default="[]"),
        sa.Column("actions", JSONB(), nullable=False, server_default="[]"),
        sa.Column("times_triggered", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("last_triggered_at", sa.DateTime(timezone=True), nullable=True),
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

    # --- automation_logs ---
    op.create_table(
        "automation_logs",
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
            index=True,
        ),
        sa.Column(
            "store_id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            nullable=False,
            index=True,
        ),
        sa.Column(
            "rule_id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            nullable=False,
            index=True,
        ),
        sa.Column("rule_name", sa.String(255), nullable=True),
        sa.Column("order_id", sa.dialects.postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("order_number", sa.String(50), nullable=True),
        sa.Column("trigger_event", sa.String(50), nullable=False),
        sa.Column("actions_executed", JSONB(), nullable=False, server_default="[]"),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("error_details", sa.Text(), nullable=True),
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
        "idx_al_store_time",
        "automation_logs",
        ["store_id", sa.text("created_at DESC")],
        schema="public",
    )

    # --- shopify_app_settings ---
    op.create_table(
        "shopify_app_settings",
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
            index=True,
        ),
        sa.Column(
            "store_id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            unique=True,
            nullable=False,
        ),
        sa.Column(
            "cod_risk_scoring_enabled",
            sa.Boolean(),
            nullable=False,
            server_default="true",
        ),
        sa.Column(
            "auto_approve_threshold", sa.Integer(), nullable=False, server_default="30"
        ),
        sa.Column(
            "auto_hold_threshold", sa.Integer(), nullable=False, server_default="70"
        ),
        sa.Column(
            "auto_cancel_threshold", sa.Integer(), nullable=False, server_default="90"
        ),
        sa.Column(
            "paymob_connected", sa.Boolean(), nullable=False, server_default="false"
        ),
        sa.Column(
            "whatsapp_connected", sa.Boolean(), nullable=False, server_default="false"
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
        schema="public",
    )


def downgrade() -> None:
    op.drop_table("shopify_app_settings", schema="public")
    op.drop_index("idx_al_store_time", table_name="automation_logs", schema="public")
    op.drop_table("automation_logs", schema="public")
    op.drop_table("automation_rules", schema="public")
    op.drop_index(
        "idx_pt_store_channel", table_name="payment_transactions", schema="public"
    )
    op.drop_table("payment_transactions", schema="public")
    op.drop_index(
        "idx_risk_store_score", table_name="risk_assessments", schema="public"
    )
    op.drop_table("risk_assessments", schema="public")
    op.drop_table("shopify_installations", schema="public")
