"""Add billing tables: invoices, discount_codes, and tenant subscription columns.

Revision ID: e5f6a7b80c12
Revises: d4e5f6a70b01
Create Date: 2026-04-11
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "e5f6a7b80c12"
down_revision: str | None = "d4e5f6a70b01"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # ─── Discount codes (must exist before invoices FK) ───────────────
    op.create_table(
        "discount_codes",
        sa.Column(
            "id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("code", sa.String(50), unique=True, nullable=False, index=True),
        sa.Column("description", sa.Text, nullable=False),
        sa.Column("type", sa.String(20), nullable=False),
        sa.Column("value", sa.Integer, nullable=False),
        sa.Column("max_uses", sa.Integer, nullable=True),
        sa.Column("current_uses", sa.Integer, nullable=False, server_default="0"),
        sa.Column(
            "applies_to_plans", sa.dialects.postgresql.ARRAY(sa.String), nullable=True
        ),
        sa.Column("valid_from", sa.DateTime(timezone=True), nullable=True),
        sa.Column("valid_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("stackable", sa.Boolean, nullable=False, server_default="false"),
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

    # ─── Invoices ─────────────────────────────────────────────────────
    op.create_table(
        "invoices",
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
        sa.Column("period_start", sa.DateTime(timezone=True), nullable=False),
        sa.Column("period_end", sa.DateTime(timezone=True), nullable=False),
        sa.Column("amount_cents", sa.Integer, nullable=False),
        sa.Column("currency", sa.String(3), nullable=False, server_default="EGP"),
        sa.Column("status", sa.String(20), nullable=False, server_default="draft"),
        sa.Column("paymob_transaction_id", sa.String(255), nullable=True),
        sa.Column(
            "discount_code_id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            sa.ForeignKey("public.discount_codes.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "discount_amount_cents", sa.Integer, nullable=False, server_default="0"
        ),
        sa.Column("paid_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True),
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

    # ─── Tenant subscription columns ──────────────────────────────────
    op.add_column(
        "tenants",
        sa.Column("paymob_customer_id", sa.String(255), nullable=True),
        schema="public",
    )
    op.add_column(
        "tenants",
        sa.Column("paymob_subscription_id", sa.String(255), nullable=True),
        schema="public",
    )
    op.add_column(
        "tenants",
        sa.Column("payment_method_last4", sa.String(4), nullable=True),
        schema="public",
    )
    op.add_column(
        "tenants",
        sa.Column("next_renewal_at", sa.DateTime(timezone=True), nullable=True),
        schema="public",
    )
    op.add_column(
        "tenants",
        sa.Column("billing_cycle", sa.String(20), nullable=True),
        schema="public",
    )
    op.add_column(
        "tenants",
        sa.Column("subscription_started_at", sa.DateTime(timezone=True), nullable=True),
        schema="public",
    )
    op.add_column(
        "tenants",
        sa.Column("cancelled_at", sa.DateTime(timezone=True), nullable=True),
        schema="public",
    )

    # ─── Seed initial discount codes ──────────────────────────────────
    op.execute("""
        INSERT INTO public.discount_codes (id, code, description, type, value, max_uses, stackable, applies_to_plans)
        VALUES
            (gen_random_uuid(), 'LAUNCH50', '50% off first 3 months for early adopters', 'percent', 50, NULL, false, ARRAY['starter','pro']),
            (gen_random_uuid(), 'FOUNDING100', '6 months free Pro for founding merchants', 'free_months', 6, 100, false, ARRAY['pro']),
            (gen_random_uuid(), 'ANNUAL10', 'Pay 10 months get 12 (annual billing)', 'percent', 17, NULL, false, ARRAY['starter','pro']),
            (gen_random_uuid(), 'STUDENT50', '50% off forever for verified students', 'percent', 50, NULL, false, ARRAY['starter','pro']),
            (gen_random_uuid(), 'REFER1FREE', '1 free month per successful referral (stackable, max 6)', 'free_months', 1, NULL, true, ARRAY['starter','pro'])
        ON CONFLICT (code) DO NOTHING;
    """)


def downgrade() -> None:
    op.drop_column("tenants", "cancelled_at", schema="public")
    op.drop_column("tenants", "subscription_started_at", schema="public")
    op.drop_column("tenants", "billing_cycle", schema="public")
    op.drop_column("tenants", "next_renewal_at", schema="public")
    op.drop_column("tenants", "payment_method_last4", schema="public")
    op.drop_column("tenants", "paymob_subscription_id", schema="public")
    op.drop_column("tenants", "paymob_customer_id", schema="public")
    op.drop_table("invoices", schema="public")
    op.drop_table("discount_codes", schema="public")
