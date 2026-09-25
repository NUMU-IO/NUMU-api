"""orders.cod_review_status: COD orders held for review.

Revision ID: cod_review_hold_20260925
Revises: app_draft_manifest_20260925
Create Date: 2026-09-25

The Trust Network's new "hold" action lets a high-risk COD order through
checkout but parks it: not booked with a courier until someone (or the COD
app) approves or rejects it. The partial index keeps the review queue a
cheap lookup however many orders a store has.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "cod_review_hold_20260925"
down_revision: str | None = "app_draft_manifest_20260925"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE public.orders ADD COLUMN IF NOT EXISTS cod_review_status varchar(16)"
    )
    op.execute(
        "ALTER TABLE public.orders ADD COLUMN IF NOT EXISTS cod_reviewed_at timestamptz"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_orders_cod_review_held ON public.orders"
        " (store_id, created_at) WHERE cod_review_status = 'held'"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS public.ix_orders_cod_review_held")
    op.execute("ALTER TABLE public.orders DROP COLUMN IF EXISTS cod_reviewed_at")
    op.execute("ALTER TABLE public.orders DROP COLUMN IF EXISTS cod_review_status")
