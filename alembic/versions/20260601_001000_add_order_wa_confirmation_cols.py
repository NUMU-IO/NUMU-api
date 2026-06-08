"""Add customer-confirmation columns to orders (COD tap-to-confirm flow)

Adds customer_confirmation_status / _requested_at + customer_confirmed_at to
public.orders so a COD order can be flagged "awaiting WhatsApp confirmation"
and flipped to confirmed when the customer taps the quick-reply button.
Field names match the merchant-hub backend-031 contract
(orders.customer_confirmation_status).

Revision ID: order_wa_confirm_cols_20260601
Revises: confirm_req_tmpl_20260601
Create Date: 2026-06-01
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "order_wa_confirm_cols_20260601"
down_revision: str | Sequence[str] | None = "confirm_req_tmpl_20260601"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "orders",
        sa.Column("customer_confirmation_status", sa.String(length=16), nullable=True),
        schema="public",
    )
    op.add_column(
        "orders",
        sa.Column(
            "customer_confirmation_requested_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
        schema="public",
    )
    op.add_column(
        "orders",
        sa.Column("customer_confirmed_at", sa.DateTime(timezone=True), nullable=True),
        schema="public",
    )


def downgrade() -> None:
    op.drop_column("orders", "customer_confirmed_at", schema="public")
    op.drop_column("orders", "customer_confirmation_requested_at", schema="public")
    op.drop_column("orders", "customer_confirmation_status", schema="public")
