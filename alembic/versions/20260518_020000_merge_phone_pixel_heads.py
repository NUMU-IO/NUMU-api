"""Merge customer_phone_idx and product_pixel_overrides heads.

Two unrelated migration lines both branched off ``is_internal_20260723``
and landed on ``dev`` in parallel:

  * ``customer_phone_idx_20260518`` (guest checkout dedup-by-phone index)
  * ``product_pixel_overrides_20260517`` (Meta pixel per-product overrides,
    via the ``product_meta_catalog_20260516`` ancestor)

This created the "Multiple head revisions are present" failure when CD
ran ``alembic upgrade head`` on the test container. They touch different
tables (``customers`` vs ``products``) so the order they apply doesn't
matter — this revision is a no-op marker that joins the two lines into
a single head so future ``upgrade head`` calls work.

Revision ID: merge_phone_pixel_20260518
Revises: customer_phone_idx_20260518, product_pixel_overrides_20260517
Create Date: 2026-05-18
"""

from collections.abc import Sequence

revision: str = "merge_phone_pixel_20260518"
down_revision: str | None = (
    "customer_phone_idx_20260518",
    "product_pixel_overrides_20260517",
)
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
