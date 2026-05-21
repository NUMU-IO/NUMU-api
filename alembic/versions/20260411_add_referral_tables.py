"""Add referral system tables.

Revision ID: f6a7b8c90d23
Revises: e5f6a7b80c12
Create Date: 2026-04-11
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "f6a7b8c90d23"
down_revision: str | None = "e5f6a7b80c12"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "merchant_referrals",
        sa.Column(
            "id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "referrer_tenant_id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            sa.ForeignKey("public.tenants.id"),
            nullable=False,
            index=True,
        ),
        sa.Column(
            "referred_tenant_id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            sa.ForeignKey("public.tenants.id"),
            nullable=False,
            unique=True,
        ),
        sa.Column("referral_code", sa.String(20), nullable=False, index=True),
        sa.Column("status", sa.String(20), nullable=False, server_default="pending"),
        sa.Column(
            "commission_rate", sa.Numeric(5, 4), nullable=False, server_default="0.0500"
        ),
        sa.Column(
            "commission_cap_cents", sa.Integer, nullable=False, server_default="500000"
        ),
        sa.Column(
            "total_commission_earned_cents",
            sa.Integer,
            nullable=False,
            server_default="0",
        ),
        sa.Column("commission_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("activated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "free_months_granted", sa.Integer, nullable=False, server_default="0"
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        schema="public",
    )

    op.create_table(
        "referral_commissions",
        sa.Column(
            "id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "referral_id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            sa.ForeignKey("public.merchant_referrals.id"),
            nullable=False,
            index=True,
        ),
        sa.Column(
            "order_id", sa.dialects.postgresql.UUID(as_uuid=True), nullable=False
        ),
        sa.Column("order_total_cents", sa.Integer, nullable=False),
        sa.Column("commission_cents", sa.Integer, nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default="pending"),
        sa.Column("confirmed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("paid_out_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("reversed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("reversal_reason", sa.String(100), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        schema="public",
    )

    op.create_table(
        "referral_payouts",
        sa.Column(
            "id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "referrer_tenant_id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            sa.ForeignKey("public.tenants.id"),
            nullable=False,
            index=True,
        ),
        sa.Column("amount_cents", sa.Integer, nullable=False),
        sa.Column("payout_method", sa.String(50), nullable=False),
        sa.Column("payout_reference", sa.String(255), nullable=True),
        sa.Column("status", sa.String(20), nullable=False, server_default="pending"),
        sa.Column("period_start", sa.DateTime(timezone=True), nullable=True),
        sa.Column("period_end", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        schema="public",
    )


def downgrade() -> None:
    op.drop_table("referral_payouts", schema="public")
    op.drop_table("referral_commissions", schema="public")
    op.drop_table("merchant_referrals", schema="public")
