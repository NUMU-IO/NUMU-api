"""Phase B of offers-v2 BOGO / tiered: targeting role + per-promotion usage limits.

Two additive columns, both nullable, both indexed by their natural
filter shape:

  • `promotion_targets.role` (text, nullable) — discriminates a target
    row as `"buy_set"` / `"get_set"` for BOGO targeting, or NULL for
    the existing global-eligibility semantics. The discount calculator
    reads role-tagged targets when building the BOGO line filters
    (which products count toward the buy threshold vs. which receive
    the discount). A column rather than a separate table because every
    BOGO promotion already creates target rows — adding a column
    avoids one more JOIN on the storefront hot path.

  • `promotions.usage_limit_total` and `promotions.usage_limit_per_customer`
    (int, nullable) — caps the number of `convert` events the
    eligibility checker will allow for this promotion. The legacy
    `Coupon.usage_limit` column only covers code-based promos; this
    pair covers automatic ones (BOGO, tiered, percent-off cart) where
    no coupon row exists.

Both columns are nullable with no server default — every existing row
keeps the historical "uncapped / no role" behavior at zero churn.

Revision ID: promo_targeting_limits_20260509
Revises: merge_heads_20260509
Create Date: 2026-05-09
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "promo_targeting_limits_20260509"
down_revision: str | None = "merge_heads_20260509"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "promotion_targets",
        sa.Column("role", sa.String(length=32), nullable=True),
        schema="public",
    )
    op.create_index(
        "ix_promotion_targets_promotion_role",
        "promotion_targets",
        ["promotion_id", "role"],
        schema="public",
    )

    op.add_column(
        "promotions",
        sa.Column("usage_limit_total", sa.Integer(), nullable=True),
        schema="public",
    )
    op.add_column(
        "promotions",
        sa.Column("usage_limit_per_customer", sa.Integer(), nullable=True),
        schema="public",
    )


def downgrade() -> None:
    op.drop_column("promotions", "usage_limit_per_customer", schema="public")
    op.drop_column("promotions", "usage_limit_total", schema="public")
    op.drop_index(
        "ix_promotion_targets_promotion_role",
        table_name="promotion_targets",
        schema="public",
    )
    op.drop_column("promotion_targets", "role", schema="public")
