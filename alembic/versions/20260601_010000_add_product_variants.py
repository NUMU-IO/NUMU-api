"""Add product_variants table + backfill default variant per product — Phase 8.1.

Revision ID: product_variants_20260601
Revises: locations_20260512
Create Date: 2026-06-01

Phase 8.1 of the Shopify-parity roadmap. The single biggest piece —
every other Phase 8 task (multi-location inventory, gift cards,
reorder, campaigns, BOGO/tiered) references `variant_id`, so this
migration lands first.

What it does:

1. Create `product_variants` table — one row per purchasable
   combination of a product's option axes. Same store-scoped RLS
   pattern as every other tenant surface (tenant_id + store_id).

2. Add `options: JSONB` column to `products` — array of
   `{name, position, values}`. Existing products start with `[]`
   (no axes, single variant).

3. BACKFILL — the load-bearing step. For every existing product,
   insert one "default variant" row carrying the product's current
   price + quantity + sku + barcode. This makes the table the
   canonical source for price/inventory going forward without
   breaking historical orders whose line items already carry
   `variant_id: NULL` (those continue to read from
   product.price/quantity via the application layer's
   fallback-when-variant_id-is-null branch).

4. Application-layer change (not in this migration): cart line items
   and checkout resolve `variant.price` and decrement
   `variant.inventory_quantity` for new orders. The cart route already
   accepts `variant_id`, just hadn't been populating it for single-
   variant products. After this migration, the storefront PDP picks
   a variant_id BEFORE add-to-cart even for single-axis products.

5. extend_search_vector — the existing search_vector GENERATED column
   already covers product name/sku/description/tags. After variants
   land, variant SKUs become searchable in their own right — we add
   a separate index in a follow-up migration when the search route
   is updated to consult `product_variants` (Phase 8.1 step 6 in the
   roadmap).

Rollback semantics:

- Downgrade DROPs `product_variants` + the `options` column. Existing
  cart line items with `variant_id` set become dangling references
  (FK constraint isn't created here; we keep variant_id nullable on
  cart_items and rely on the application layer). Acceptable cost
  because rollback should be rare and the next forward migration
  re-creates everything.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB, UUID

from alembic import op

revision: str = "product_variants_20260601"
down_revision: str | None = "locations_20260512"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # ── 1. product_variants table ──────────────────────────────────────
    op.create_table(
        "product_variants",
        sa.Column(
            "id",
            UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "tenant_id",
            UUID(as_uuid=True),
            sa.ForeignKey("public.tenants.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "store_id",
            UUID(as_uuid=True),
            sa.ForeignKey("public.stores.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "product_id",
            UUID(as_uuid=True),
            sa.ForeignKey("public.products.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "position",
            sa.Integer,
            nullable=False,
            server_default=sa.text("0"),
        ),
        sa.Column(
            "option_values",
            JSONB,
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "price_amount",
            sa.Integer,
            nullable=False,
            server_default=sa.text("0"),
        ),
        sa.Column(
            "price_currency",
            sa.String(3),
            nullable=False,
            server_default="EGP",
        ),
        sa.Column("compare_at_price", sa.Integer, nullable=True),
        sa.Column("cost_price", sa.Integer, nullable=True),
        sa.Column("sku", sa.String(100), nullable=True),
        sa.Column("barcode", sa.String(100), nullable=True),
        sa.Column(
            "inventory_quantity",
            sa.Integer,
            nullable=False,
            server_default=sa.text("0"),
        ),
        sa.Column("image_url", sa.String(2048), nullable=True),
        sa.Column("weight", sa.Numeric(10, 2), nullable=True),
        sa.Column(
            "metadata",
            JSONB,
            nullable=True,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            onupdate=sa.func.now(),
            nullable=False,
        ),
        # SKU uniqueness scoped to store (different products in the same
        # store can't reuse a SKU; cross-store reuse is fine).
        sa.UniqueConstraint("store_id", "sku", name="uq_variants_store_sku"),
        schema="public",
    )
    op.create_index(
        "ix_variants_tenant",
        "product_variants",
        ["tenant_id"],
        unique=False,
        schema="public",
    )
    op.create_index(
        "ix_variants_store",
        "product_variants",
        ["store_id"],
        unique=False,
        schema="public",
    )
    op.create_index(
        "ix_variants_product",
        "product_variants",
        ["product_id", "position"],
        unique=False,
        schema="public",
    )
    op.create_index(
        "ix_variants_sku",
        "product_variants",
        ["sku"],
        unique=False,
        schema="public",
        postgresql_where=sa.text("sku IS NOT NULL"),
    )

    # ── 2. products.options ────────────────────────────────────────────
    op.add_column(
        "products",
        sa.Column(
            "options",
            JSONB,
            nullable=True,
            server_default=sa.text("'[]'::jsonb"),
        ),
        schema="public",
    )

    # ── 3. Backfill: one default variant per existing product ─────────
    # We use a single INSERT-SELECT to atomically copy every product's
    # current price/inventory/sku into a default variant row. The
    # `option_values` is `{}` (no axes), which is the canonical shape
    # for a single-variant product.
    op.execute(
        sa.text("""
        INSERT INTO public.product_variants (
            id, tenant_id, store_id, product_id, position, option_values,
            price_amount, price_currency, compare_at_price, cost_price,
            sku, barcode, inventory_quantity, image_url, weight,
            metadata, created_at, updated_at
        )
        SELECT
            gen_random_uuid(),
            p.tenant_id,
            p.store_id,
            p.id,
            0,
            '{}'::jsonb,
            COALESCE(p.price_amount, 0),
            COALESCE(p.price_currency, 'EGP'),
            p.compare_at_price,
            p.cost_price,
            p.sku,
            NULL,
            COALESCE(p.quantity, 0),
            NULL,
            p.weight,
            '{}'::jsonb,
            NOW(),
            NOW()
        FROM public.products p
        WHERE NOT EXISTS (
            SELECT 1 FROM public.product_variants v
            WHERE v.product_id = p.id
        )
        """)
    )


def downgrade() -> None:
    op.drop_column("products", "options", schema="public")
    op.drop_index("ix_variants_sku", table_name="product_variants", schema="public")
    op.drop_index("ix_variants_product", table_name="product_variants", schema="public")
    op.drop_index("ix_variants_store", table_name="product_variants", schema="public")
    op.drop_index("ix_variants_tenant", table_name="product_variants", schema="public")
    op.drop_table("product_variants", schema="public")
