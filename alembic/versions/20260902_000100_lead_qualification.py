"""Qualification answers on merchant leads, and a backfill for activation.

Two unrelated-looking changes that are really the same one: making the
lead record answer "who is this merchant, and did they become a business".

**Qualification.** The onboarding wizard already asks what the merchant
sells, but only to auto-configure the store — the answer was applied and
thrown away, so "how many of our merchants sell fashion" was unanswerable
even though every merchant had answered it. These four columns keep the
answers. ``sells_where_today`` and ``monthly_orders_band`` are new
questions; they decide whether a human should call this merchant and
which pitch they get (a Shopify seller doing 200 orders a month is a
migration, an Instagram seller doing 5 is a first store).

**Activation backfill.** ``first_order_at`` and status ``activated`` have
a writer as of this deploy, but every merchant who activated *before* it
would read as un-activated forever, which would make the funnel look
like a cliff exactly where the product starts working. Backfilled from
the earliest paid order per tenant.

Revision ID: lead_qualification_20260902
Revises: merchant_leads_20260902
"""

import sqlalchemy as sa

from alembic import op

revision = "lead_qualification_20260902"
down_revision = "merchant_leads_20260902"
branch_labels = None
depends_on = None

_COLUMNS = (
    # Matches the wizard's niche ids (fashion, electronics, beauty, home,
    # food, accessories, other). Not an enum: adding a category should not
    # need a migration.
    ("sells_what", "VARCHAR(32)"),
    # instagram, shopify, zid, salla, own_site, offline, nowhere, other.
    ("sells_where_today", "VARCHAR(32)"),
    # Bands, not a number: merchants estimate, and a band is honest about
    # that while still segmenting well enough to route a sales call.
    ("monthly_orders_band", "VARCHAR(20)"),
    ("city", "VARCHAR(80)"),
)


def upgrade() -> None:
    for name, ddl_type in _COLUMNS:
        op.execute(
            sa.text(
                f"ALTER TABLE public.merchant_leads "
                f"ADD COLUMN IF NOT EXISTS {name} {ddl_type}"
            )
        )

    # Segmentation queries are "all leads selling fashion who do 200+
    # orders" — a composite over the two fields that actually drive
    # routing. Partial, because unanswered rows are the majority early on
    # and are never the ones being segmented.
    op.execute(
        sa.text(
            "CREATE INDEX IF NOT EXISTS ix_merchant_leads_qualification "
            "ON public.merchant_leads (sells_what, monthly_orders_band) "
            "WHERE sells_what IS NOT NULL"
        )
    )

    # ── Activation backfill ───────────────────────────────────────
    # Earliest paid order per tenant. paid_at can be NULL on older rows
    # that were marked paid before the column existed, so fall back to
    # created_at rather than dropping the merchant from the funnel.
    op.execute(
        sa.text(
            """
            UPDATE public.merchant_leads l
            SET first_order_at = f.first_paid,
                status = 'activated'
            FROM (
                SELECT o.tenant_id,
                       MIN(COALESCE(o.paid_at, o.created_at)) AS first_paid
                FROM public.orders o
                WHERE o.payment_status = 'PAID'
                GROUP BY o.tenant_id
            ) f
            WHERE f.tenant_id = l.tenant_id
              AND l.first_order_at IS NULL
            """
        )
    )


def downgrade() -> None:
    op.execute(sa.text("DROP INDEX IF EXISTS public.ix_merchant_leads_qualification"))
    for name, _ in _COLUMNS:
        op.execute(
            sa.text(f"ALTER TABLE public.merchant_leads DROP COLUMN IF EXISTS {name}")
        )
