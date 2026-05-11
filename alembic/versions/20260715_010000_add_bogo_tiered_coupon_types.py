"""Add BUY_X_GET_Y + TIERED coupon types + config column — Phase 8.4.

Revision ID: bogo_tiered_20260715
Revises: gift_cards_20260701
Create Date: 2026-07-15

Phase 8.4 of the Shopify-parity roadmap. Two new coupon types
backed by a flexible `config` JSONB column:

* `buy_x_get_y` — "Buy 2 get 1 free" / "Buy 3 get 1 at 50% off".
  Config: `{buy_quantity, get_quantity, get_discount_percentage,
           buy_product_ids?, get_product_ids?}`.
* `tiered` — "Spend $50 get 10% off, spend $100 get 15% off".
  Config: `{tiers: [{min_subtotal_cents, discount_percentage}, ...]}`.

The discount calculator (in `core/entities/coupon.py`) reads the
config when the coupon's type matches. Simple types (PERCENTAGE /
FIXED / FREE_SHIPPING) leave `config` NULL.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

from alembic import op

revision: str = "bogo_tiered_20260715"
down_revision: str | None = "gift_cards_20260701"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # 1. Extend the existing coupontype enum with BUY_X_GET_Y + TIERED.
    #    Postgres ALTER TYPE ADD VALUE doesn't accept multiple values
    #    in one statement, so we emit two ALTERs. `IF NOT EXISTS`
    #    makes the migration idempotent.
    op.execute(
        sa.text("ALTER TYPE public.coupontype ADD VALUE IF NOT EXISTS 'buy_x_get_y'")
    )
    op.execute(sa.text("ALTER TYPE public.coupontype ADD VALUE IF NOT EXISTS 'tiered'"))

    # 2. Add the JSONB config column. Nullable — simple types leave
    #    it NULL.
    op.add_column(
        "coupons",
        sa.Column("config", JSONB, nullable=True),
        schema="public",
    )


def downgrade() -> None:
    # Drop the column. We do NOT shrink the enum back (Postgres doesn't
    # support ALTER TYPE DROP VALUE without recreating the type) — the
    # extra enum values are harmless when unused.
    op.drop_column("coupons", "config", schema="public")
