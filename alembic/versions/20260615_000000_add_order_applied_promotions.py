"""Add applied_promotions JSONB column to orders.

Revision ID: order_applied_promos_20260615
Revises: merge_heads_20260607
Create Date: 2026-06-15

Commerce-correctness Phase 1. The offers-v2 engine
(``CalculateCartDiscountsUseCase`` / ``DiscountCalculator``) can now run at
checkout time behind the ``ff_apply_offers_at_checkout`` flag. When it applies
automatic / free-shipping promotions to an order we snapshot them here so the
order read can render them without re-running the calculator.

Shape: JSONB list of ``{"id", "title", "title_ar"?, "amount"}`` where
``amount`` is integer cents. NOT NULL with a ``'[]'`` server default so legacy
rows and the flag-off path carry an empty list rather than NULL. Not a foreign
key — promotions can be edited/removed later and this is an immutable
order-time snapshot (same rationale as the ``line_items`` JSONB).
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "order_applied_promos_20260615"
down_revision: str = "merge_heads_20260607"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "orders",
        sa.Column(
            "applied_promotions",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        schema="public",
    )


def downgrade() -> None:
    op.drop_column("orders", "applied_promotions", schema="public")
