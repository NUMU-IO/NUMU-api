"""Founder-merchant cohort on tenants.

Marks the merchants who were on NUMU at the start, so the hub can show a
founder badge beside their store name.

Stores the JOIN YEAR ("2025"), not a rank. A rank would tell merchant #42
that 41 came before them — publishing the platform's size to every merchant
and, once the badge reaches a storefront, to every shopper. A year carries
the same "I was here early" meaning and reveals nothing about volume.
Storing it per merchant rather than deriving it from a counter also means
there is no count in the schema to leak by accident.

VARCHAR(4) rather than an integer year: it is a label, never arithmetic, and
leaving room for a non-numeric cohort key later costs nothing now.

Revision ID: tenant_founder_cohort_20260828
Revises: product_commerce_20260824
"""

import sqlalchemy as sa

from alembic import op

revision = "tenant_founder_cohort_20260828"
down_revision = "product_commerce_20260824"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # IF NOT EXISTS: prod has picked up hand-created columns and indexes
    # before, and a migration that cannot be re-run is a migration that
    # blocks a deploy at the worst possible moment.
    op.execute(
        sa.text(
            "ALTER TABLE public.tenants "
            "ADD COLUMN IF NOT EXISTS founder_cohort VARCHAR(4)"
        )
    )
    # Partial index: the badge query only ever asks for the founders, and
    # they are a permanently small slice of the table.
    op.execute(
        sa.text(
            "CREATE INDEX IF NOT EXISTS ix_tenants_founder_cohort "
            "ON public.tenants (founder_cohort) "
            "WHERE founder_cohort IS NOT NULL"
        )
    )


def downgrade() -> None:
    op.execute(sa.text("DROP INDEX IF EXISTS public.ix_tenants_founder_cohort"))
    op.execute(
        sa.text("ALTER TABLE public.tenants DROP COLUMN IF EXISTS founder_cohort")
    )
