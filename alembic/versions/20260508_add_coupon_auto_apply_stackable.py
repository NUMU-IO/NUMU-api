"""Add coupon auto-apply + stackable flags (Phase 3.8).

Revision ID: coupon_phase_3_8_20260508
Revises: order_returns_20260508
Create Date: 2026-05-08

Adds two boolean columns to `coupons`:
  - is_auto_apply  → checkout auto-includes the coupon when conditions
                     are met (no customer-entered code needed)
  - stackable      → coupon can combine with another stackable coupon
                     on the same order

Both default to false so pre-Phase-3.8 coupons continue to behave as
single-application, customer-code-required promos.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "coupon_phase_3_8_20260508"
down_revision: str | None = "order_returns_20260508"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "coupons",
        sa.Column(
            "is_auto_apply",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
        schema="public",
    )
    op.add_column(
        "coupons",
        sa.Column(
            "stackable",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
        schema="public",
    )

    # Hot-path index: checkout's auto-apply scan filters by
    # (store_id, is_auto_apply=true, is_active=true). The composite
    # index makes this cheap even for stores with thousands of coupons.
    op.create_index(
        "ix_coupons_store_auto_apply",
        "coupons",
        ["store_id"],
        unique=False,
        schema="public",
        postgresql_where=sa.text("is_auto_apply = true AND is_active = true"),
    )


def downgrade() -> None:
    op.drop_index(
        "ix_coupons_store_auto_apply",
        table_name="coupons",
        schema="public",
    )
    op.drop_column("coupons", "stackable", schema="public")
    op.drop_column("coupons", "is_auto_apply", schema="public")
