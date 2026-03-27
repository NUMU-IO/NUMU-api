"""Add NUMU Trust Network tables and columns.

Creates:
  - network_reputation (public)
  - network_contribution_log (public)
  - payment_link_sessions (public)

Alters:
  - risk_assessments: add score_type, scored_at
  - shopify_app_settings: add whatsapp_template_id, whatsapp_nudge_enabled

Revision ID: a1b2c3d4e5f6
Revises: (head)
Create Date: 2026-03-27
"""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "a1b2c3d4e5f6"
down_revision = None  # Set to latest existing revision when running
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ── network_reputation ───────────────────────────────────────
    op.create_table(
        "network_reputation",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("phone_hash", sa.String(64), unique=True, nullable=False, index=True),
        sa.Column(
            "total_network_orders", sa.Integer(), nullable=False, server_default="0"
        ),
        sa.Column(
            "total_network_rtos", sa.Integer(), nullable=False, server_default="0"
        ),
        sa.Column(
            "total_successful_deliveries",
            sa.Integer(),
            nullable=False,
            server_default="0",
        ),
        sa.Column("total_refunds", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "contributing_store_count", sa.Integer(), nullable=False, server_default="0"
        ),
        sa.Column("network_risk_score", sa.Integer(), nullable=True),
        sa.Column(
            "confidence_level", sa.String(20), nullable=False, server_default="'low'"
        ),
        sa.Column(
            "first_seen_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("last_order_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_rto_at", sa.DateTime(timezone=True), nullable=True),
        schema="public",
    )

    # ── network_contribution_log ─────────────────────────────────
    op.create_table(
        "network_contribution_log",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("store_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("phone_hash", sa.String(64), nullable=False),
        sa.Column("event_type", sa.String(20), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        schema="public",
    )
    op.create_index(
        "ix_ncl_store_id", "network_contribution_log", ["store_id"], schema="public"
    )
    op.create_index(
        "ix_ncl_phone_hash", "network_contribution_log", ["phone_hash"], schema="public"
    )

    # ── payment_link_sessions ────────────────────────────────────
    op.create_table(
        "payment_link_sessions",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "store_id", postgresql.UUID(as_uuid=True), nullable=False, index=True
        ),
        sa.Column("order_id", postgresql.UUID(as_uuid=True), nullable=True, index=True),
        sa.Column("shopify_order_id", sa.String(255), nullable=True),
        sa.Column("amount_cents", sa.Integer(), nullable=False),
        sa.Column("currency", sa.String(3), nullable=False, server_default="'EGP'"),
        sa.Column("status", sa.String(20), nullable=False, server_default="'pending'"),
        sa.Column("available_gateways", postgresql.JSONB(), nullable=False),
        sa.Column("merchant_branding", postgresql.JSONB(), nullable=True),
        sa.Column("gateway_used", sa.String(50), nullable=True),
        sa.Column("gateway_transaction_id", sa.String(255), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        schema="public",
    )

    # ── risk_assessments: add score_type, scored_at ──────────────
    op.add_column(
        "risk_assessments",
        sa.Column(
            "score_type", sa.String(20), nullable=False, server_default="'preliminary'"
        ),
        schema="public",
    )
    op.add_column(
        "risk_assessments",
        sa.Column("scored_at", sa.DateTime(timezone=True), nullable=True),
        schema="public",
    )

    # ── shopify_app_settings: add whatsapp fields ────────────────
    op.add_column(
        "shopify_app_settings",
        sa.Column("whatsapp_template_id", sa.String(255), nullable=True),
        schema="public",
    )
    op.add_column(
        "shopify_app_settings",
        sa.Column(
            "whatsapp_nudge_enabled",
            sa.Boolean(),
            nullable=False,
            server_default="false",
        ),
        schema="public",
    )


def downgrade() -> None:
    op.drop_column("shopify_app_settings", "whatsapp_nudge_enabled", schema="public")
    op.drop_column("shopify_app_settings", "whatsapp_template_id", schema="public")
    op.drop_column("risk_assessments", "scored_at", schema="public")
    op.drop_column("risk_assessments", "score_type", schema="public")
    op.drop_table("payment_link_sessions", schema="public")
    op.drop_index(
        "ix_ncl_phone_hash", table_name="network_contribution_log", schema="public"
    )
    op.drop_index(
        "ix_ncl_store_id", table_name="network_contribution_log", schema="public"
    )
    op.drop_table("network_contribution_log", schema="public")
    op.drop_table("network_reputation", schema="public")
