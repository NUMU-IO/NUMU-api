"""Add AWAITING_PAYMENT to the orderstatus enum.

Revision ID: awaiting_payment_20260923
Revises: paid_apps_20260921
Create Date: 2026-09-23

Storefront card-gateway orders (Kashier, Paymob, Moyasar, Fawaterak) now sit
in AWAITING_PAYMENT from checkout until the gateway webhook confirms payment,
hidden from the merchant and without notifications. Uppercase label because
OrderModel.status has no values_callable (see pending_dep_upper_20260426).
"""

from collections.abc import Sequence

from alembic import op

revision: str = "awaiting_payment_20260923"
down_revision: str | None = "paid_apps_20260921"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        "ALTER TYPE public.orderstatus ADD VALUE IF NOT EXISTS 'AWAITING_PAYMENT'"
    )


def downgrade() -> None:
    # PostgreSQL does not support removing enum values.
    pass
