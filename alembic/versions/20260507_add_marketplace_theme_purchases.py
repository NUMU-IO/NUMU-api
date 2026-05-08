"""Add marketplace_theme_purchases for paid-theme licensing.

Revision ID: marketplace_purchases_20260507
Revises: merge_theme_heads_20260505
Create Date: 2026-05-07

Adds the table that records every paid-theme purchase. A successful,
non-refunded row grants the buyer install rights across all their
stores (Shopify-equivalent: one purchase = developer-unlimited installs
under the same merchant account). The Stripe payment intent + charge
IDs let the webhook idempotently mark a row as paid, and the
``refunded_amount_cents`` field supports both partial and full refunds
without rewriting status semantics.

Uniqueness: ``stripe_payment_intent_id`` is unique so the webhook is
safe to replay (Stripe reissues the same event after transient errors).
There's no ``UNIQUE(user_id, marketplace_theme_id)`` because a user may
re-purchase after refund — the service layer enforces "no duplicate
*active* purchase" in code.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB, UUID

from alembic import op

revision: str = "marketplace_purchases_20260507"
down_revision: str | None = "merge_theme_heads_20260505"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "marketplace_theme_purchases",
        sa.Column(
            "id",
            UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "user_id",
            UUID(as_uuid=True),
            sa.ForeignKey("public.users.id", ondelete="RESTRICT"),
            nullable=False,
            index=True,
        ),
        sa.Column(
            "marketplace_theme_id",
            UUID(as_uuid=True),
            sa.ForeignKey("public.marketplace_themes.id", ondelete="RESTRICT"),
            nullable=False,
            index=True,
        ),
        sa.Column("amount_cents", sa.Integer(), nullable=False),
        sa.Column(
            "currency",
            sa.String(length=10),
            nullable=False,
            server_default="USD",
        ),
        sa.Column(
            "stripe_payment_intent_id",
            sa.String(length=255),
            nullable=True,
            unique=True,
        ),
        sa.Column(
            "stripe_charge_id",
            sa.String(length=255),
            nullable=True,
        ),
        sa.Column(
            "status",
            sa.String(length=50),
            nullable=False,
            server_default="pending",
        ),
        sa.Column(
            "refunded_amount_cents",
            sa.Integer(),
            nullable=False,
            server_default="0",
        ),
        sa.Column(
            "refund_reason",
            sa.Text(),
            nullable=True,
        ),
        sa.Column(
            "metadata",
            JSONB(),
            nullable=False,
            server_default="{}",
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("NOW()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("NOW()"),
        ),
        schema="public",
    )

    # Composite index for the hot install-time query: "does user X have a
    # succeeded, non-refunded purchase for theme Y?"
    op.create_index(
        "ix_mtp_user_theme_status",
        "marketplace_theme_purchases",
        ["user_id", "marketplace_theme_id", "status"],
        schema="public",
    )


def downgrade() -> None:
    op.drop_index(
        "ix_mtp_user_theme_status",
        table_name="marketplace_theme_purchases",
        schema="public",
    )
    op.drop_table("marketplace_theme_purchases", schema="public")
