"""Add ttclid to customer_touches.

A TikTok ad click appends only ``ttclid`` to the landing URL — no UTMs —
and the touch contract only knew ``gclid`` / ``fbclid``. So a TikTok
landing recorded no touch at all: the journey never showed the TikTok
visit, and every downstream consumer (funnel rows, the order, the
abandoned checkout, the ``fbc`` synthesised for Meta CAPI) inherited the
previous click — usually a Meta one — instead.

Idempotent: ADD COLUMN IF NOT EXISTS, safe to re-run.

Revision ID: touch_ttclid_20260821
Revises: chconn_linked_page_20260821
Create Date: 2026-08-21
"""

from alembic import op

# revision identifiers, used by Alembic.
revision = "touch_ttclid_20260821"
down_revision = "chconn_linked_page_20260821"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        ALTER TABLE public.customer_touches
        ADD COLUMN IF NOT EXISTS ttclid VARCHAR(256)
        """
    )


def downgrade() -> None:
    op.execute("ALTER TABLE public.customer_touches DROP COLUMN IF EXISTS ttclid")
