"""Merchant-recorded part payments on the order page.

Revision ID: proof_recorded_method_20260920
Revises: wa_paid_access_20260916
Create Date: 2026-09-20

The merchant can now record an out-of-band payment (Vodafone Cash, InstaPay,
cash, bank transfer) against an order from the order detail page, attaching the
receipt screenshot and typing the amount actually paid. Each such payment is a
row in the existing ``payment_proofs`` table, so no new table is needed.

``recorded_method`` carries the rail the merchant picked. It is NULL on every
customer-submitted proof, so a non-NULL value also identifies the row as
merchant-recorded — one column covering both jobs.

Additive and nullable: every existing row keeps working unchanged, and the
customer-facing proof + OCR pipeline never reads this column.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "proof_recorded_method_20260920"
down_revision: str | Sequence[str] | None = "wa_paid_access_20260916"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "payment_proofs",
        sa.Column("recorded_method", sa.String(32), nullable=True),
        schema="public",
    )


def downgrade() -> None:
    op.drop_column("payment_proofs", "recorded_method", schema="public")
