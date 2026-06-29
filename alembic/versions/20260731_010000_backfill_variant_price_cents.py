"""Backfill variant prices stored in major units → cents.

Revision ID: backfill_variant_cents_20260731
Revises: personal_access_tokens_20260730
Create Date: 2026-07-31

The variant repository historically treated ``product_variants.price_amount``
as MAJOR units while ``products`` (and cart/checkout/storefront) treat it as
CENTS. Variants written via the hub admin route therefore stored e.g. ``220``
(220 EGP as if major) where ``22000`` (cents) was expected, so the storefront
under-read them 100× (showing 2.20 instead of 220).

The repository is now aligned to the cents convention (read ``from_cents``,
write ``.cents``). This migration backfills the rows that were written under
the old convention: single-variant products whose lone variant price is
~100× smaller than the product price are synced to the product's cents price
(a single default variant must mirror its product). Idempotent — once synced
the WHERE no longer matches, and rows already in cents are untouched.

Production data was corrected out-of-band before this migration shipped; this
makes the fix reproducible for every other environment and a fresh restore.
"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "backfill_variant_cents_20260731"
down_revision: str = "personal_access_tokens_20260730"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


# Only touch single-variant products whose lone variant is ~100× cheaper than
# the product (the major-as-cents signature). The 50–150 ratio band avoids
# legitimately-cheaper variants and never widens a genuine price difference.
_BACKFILL_SQL = """
UPDATE public.product_variants v
SET price_amount = p.price_amount,
    updated_at = now()
FROM public.products p
WHERE p.id = v.product_id
  AND v.price_amount > 0
  AND v.price_amount <> p.price_amount
  AND (SELECT count(*) FROM public.product_variants v2
       WHERE v2.product_id = p.id) = 1
  AND (p.price_amount::numeric / v.price_amount) BETWEEN 50 AND 150
"""


def upgrade() -> None:
    op.execute(_BACKFILL_SQL)


def downgrade() -> None:
    # Data correction — not reversible (the original major values are lost and
    # were incorrect anyway). No-op.
    pass
