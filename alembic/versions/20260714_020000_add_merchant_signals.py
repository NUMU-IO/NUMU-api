"""Add merchant_signals — the AI Commerce Intelligence write model.

Every piece of advice / opportunity / alert the rule engine produces is
one row here. Rows store the rule id + a metrics JSONB snapshot (copy is
rendered from templates at read time) and a computed expected-impact
figure used for ranking.

Revision ID: merchant_signals_20260714
Revises: metric_targets_20260714
Create Date: 2026-07-14
"""

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB, UUID

from alembic import op

revision: str = "merchant_signals_20260714"
down_revision: str | None = "metric_targets_20260714"
branch_labels: tuple[str, ...] | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.create_table(
        "merchant_signals",
        sa.Column(
            "id",
            UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "tenant_id",
            UUID(as_uuid=True),
            sa.ForeignKey("public.tenants.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "store_id",
            UUID(as_uuid=True),
            sa.ForeignKey("public.stores.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("kind", sa.String(20), nullable=False, server_default="advice"),
        sa.Column("rule_id", sa.String(50), nullable=False),
        sa.Column("severity", sa.String(20), nullable=False, server_default="info"),
        sa.Column("status", sa.String(20), nullable=False, server_default="active"),
        sa.Column("metrics_snapshot", JSONB, nullable=True),
        sa.Column("expected_impact_cents", sa.BigInteger, nullable=True),
        sa.Column("cooldown_until", sa.DateTime(timezone=True), nullable=True),
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
        "ix_merchant_signals_store_id",
        "merchant_signals",
        ["store_id"],
        schema="public",
    )
    op.create_index(
        "ix_merchant_signals_store_status",
        "merchant_signals",
        ["store_id", "status"],
        schema="public",
    )
    op.create_index(
        "ix_merchant_signals_status",
        "merchant_signals",
        ["status"],
        schema="public",
    )
    # One ACTIVE signal per (store, rule) — re-fires refresh the existing
    # row instead of stacking duplicates.
    op.create_index(
        "uq_merchant_signals_store_rule_active",
        "merchant_signals",
        ["store_id", "rule_id"],
        unique=True,
        schema="public",
        postgresql_where=sa.text("status = 'active'"),
    )


def downgrade() -> None:
    op.drop_index(
        "uq_merchant_signals_store_rule_active",
        table_name="merchant_signals",
        schema="public",
    )
    op.drop_index(
        "ix_merchant_signals_status", table_name="merchant_signals", schema="public"
    )
    op.drop_index(
        "ix_merchant_signals_store_status",
        table_name="merchant_signals",
        schema="public",
    )
    op.drop_index(
        "ix_merchant_signals_store_id", table_name="merchant_signals", schema="public"
    )
    op.drop_table("merchant_signals", schema="public")
