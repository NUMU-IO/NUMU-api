"""Add customer_trust columns + auto-approve settings (backend-022).

* ``risk_assessments``: ``customer_trust``, ``trust_tier``, ``negative_adjustment_count``
* ``shopify_app_settings``: ``auto_approve_on_trust_enabled``,
  ``auto_approve_trust_threshold``, ``auto_disabled_at``,
  ``auto_disabled_reason``, ``first_recovery_celebration_dismissed``,
  ``recovery_enabled``

All columns are additive; existing rows pick up the server defaults.

Revision ID: customer_trust_20260511
Revises: recovery_flow_20260511
Create Date: 2026-05-11
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "customer_trust_20260511"
down_revision: str | None = "recovery_flow_20260511"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # risk_assessments — trust factor outputs
    op.add_column(
        "risk_assessments",
        sa.Column("customer_trust", sa.Integer, nullable=True),
        schema="public",
    )
    op.add_column(
        "risk_assessments",
        sa.Column("trust_tier", sa.String(10), nullable=True),
        schema="public",
    )
    op.add_column(
        "risk_assessments",
        sa.Column("negative_adjustment_count", sa.Integer, nullable=True),
        schema="public",
    )
    op.add_column(
        "risk_assessments",
        sa.Column("customer_phone_hash", sa.String(64), nullable=True),
        schema="public",
    )
    op.create_index(
        "ix_risk_assessments_customer_phone_hash",
        "risk_assessments",
        ["customer_phone_hash"],
        schema="public",
    )

    # shopify_app_settings — auto-approve + recovery toggles
    op.add_column(
        "shopify_app_settings",
        sa.Column(
            "auto_approve_on_trust_enabled",
            sa.Boolean,
            server_default=sa.text("false"),
            nullable=False,
        ),
        schema="public",
    )
    op.add_column(
        "shopify_app_settings",
        sa.Column(
            "auto_approve_trust_threshold",
            sa.Integer,
            server_default=sa.text("80"),
            nullable=False,
        ),
        schema="public",
    )
    op.add_column(
        "shopify_app_settings",
        sa.Column("auto_disabled_at", sa.DateTime(timezone=True), nullable=True),
        schema="public",
    )
    op.add_column(
        "shopify_app_settings",
        sa.Column("auto_disabled_reason", sa.String(512), nullable=True),
        schema="public",
    )
    op.add_column(
        "shopify_app_settings",
        sa.Column(
            "first_recovery_celebration_dismissed",
            sa.Boolean,
            server_default=sa.text("false"),
            nullable=False,
        ),
        schema="public",
    )
    op.add_column(
        "shopify_app_settings",
        sa.Column(
            "recovery_enabled",
            sa.Boolean,
            server_default=sa.text("true"),
            nullable=False,
        ),
        schema="public",
    )


def downgrade() -> None:
    op.drop_index(
        "ix_risk_assessments_customer_phone_hash",
        table_name="risk_assessments",
        schema="public",
    )
    op.drop_column("shopify_app_settings", "recovery_enabled", schema="public")
    op.drop_column(
        "shopify_app_settings",
        "first_recovery_celebration_dismissed",
        schema="public",
    )
    op.drop_column("shopify_app_settings", "auto_disabled_reason", schema="public")
    op.drop_column("shopify_app_settings", "auto_disabled_at", schema="public")
    op.drop_column(
        "shopify_app_settings", "auto_approve_trust_threshold", schema="public"
    )
    op.drop_column(
        "shopify_app_settings", "auto_approve_on_trust_enabled", schema="public"
    )
    op.drop_column("risk_assessments", "customer_phone_hash", schema="public")
    op.drop_column("risk_assessments", "negative_adjustment_count", schema="public")
    op.drop_column("risk_assessments", "trust_tier", schema="public")
    op.drop_column("risk_assessments", "customer_trust", schema="public")
