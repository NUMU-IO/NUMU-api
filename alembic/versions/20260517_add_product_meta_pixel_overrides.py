"""Wave 2 Phase 13.2 — per-product pixel overrides.

Revision ID: product_pixel_overrides_20260517
Revises: product_meta_catalog_20260516
Create Date: 2026-05-17

Adds a nullable ``meta_pixel_overrides`` JSONB column to ``products``.
Schema of the value (when set):

    {
      "override_mode": "exclusive" | "additive",
      "pixels": [
        {"pixel_id": "111111111111111", "label": "Agency-A"},
        {"pixel_id": "222222222222222", "label": "Agency-B"}
      ]
    }

  * ``exclusive``: events for THIS product fire ONLY on the override
    pixels; the store-level pixels are skipped. Used when a media
    buyer has dedicated this SKU to an agency-owned pixel and doesn't
    want the store-level pixel to also receive the event (dedup math).

  * ``additive``: events fire on BOTH the store-level pixels AND the
    override pixels. Used when the override is supplementary (e.g.,
    a brand-specific retargeting pixel layered on top of the
    merchant's main pixel).

Null = no override (the store-level ``pixels[]`` resolver result is
used as-is). Matches the EasyOrders per-product custom pixel feature
(plan Reference appendix §10 / §7) which is unique among MENA platforms.

No index — this column is only read alongside the rest of the product
row when /track fires for a specific product_id. The product row is
already in the buffer cache from the surrounding product fetch.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "product_pixel_overrides_20260517"
down_revision: str | None = "product_meta_catalog_20260516"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "products",
        sa.Column(
            "meta_pixel_overrides",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
        schema="public",
    )


def downgrade() -> None:
    op.drop_column("products", "meta_pixel_overrides", schema="public")
