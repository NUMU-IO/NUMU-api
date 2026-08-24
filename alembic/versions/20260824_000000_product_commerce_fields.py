"""Product commerce fields: unlisted, shipping, tax, scheduled sale, related.

Five merchant-facing controls that had no storage:

  * ``UNLISTED`` product status — reachable by direct link, absent from
    listings, search, feeds and the sitemap. Previously a product was
    either fully public or fully hidden.
  * ``requires_shipping`` — a digital product should not ask a buyer for
    an address or add a shipping fee.
  * ``tax_exempt`` — some goods are zero-rated; tax was computed on every
    line unconditionally.
  * ``sale_price`` + a window — a discount that starts and ends on its
    own. ``compare_at_price`` could express "was/now" but never *when*.
  * ``related_product_ids`` — a curated similar-products list, overriding
    the automatic one.

Revision ID: product_commerce_20260824
Revises: orders_collected_total_20260823
"""

from alembic import op

revision = "product_commerce_20260824"
down_revision = "orders_collected_total_20260823"
branch_labels = None
depends_on = None


# Every statement is IF NOT EXISTS / IF EXISTS: production has had columns
# hand-created ahead of a migration before, and a partially-applied state
# must not brick the deploy.
_COLUMNS = (
    ("requires_shipping", "BOOLEAN NOT NULL DEFAULT true"),
    ("tax_exempt", "BOOLEAN NOT NULL DEFAULT false"),
    # Cents, matching price_amount. NULL = no sale configured.
    ("sale_price", "INTEGER"),
    ("sale_starts_at", "TIMESTAMPTZ"),
    ("sale_ends_at", "TIMESTAMPTZ"),
    # JSONB list of product-id strings. JSONB rather than UUID[] to match
    # how every other curated list on this table is stored.
    ("related_product_ids", "JSONB"),
)


def upgrade() -> None:
    # PG 12+ permits ADD VALUE inside a transaction as long as the new
    # value is not USED in the same transaction. Nothing below writes an
    # UNLISTED row, so this is safe here.
    #
    # Stored UPPERCASE: `productstatus` is not one of the enums carrying
    # `values_callable`, so SQLAlchemy persists member NAMES.
    op.execute("ALTER TYPE public.productstatus ADD VALUE IF NOT EXISTS 'UNLISTED'")

    for name, ddl in _COLUMNS:
        op.execute(f"ALTER TABLE public.products ADD COLUMN IF NOT EXISTS {name} {ddl}")

    # Partial index: the storefront's "is this on sale right now" filter
    # only ever looks at rows that actually have a sale price.
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_products_sale_window "
        "ON public.products (sale_starts_at, sale_ends_at) "
        "WHERE sale_price IS NOT NULL"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS public.ix_products_sale_window")
    for name, _ in _COLUMNS:
        op.execute(f"ALTER TABLE public.products DROP COLUMN IF EXISTS {name}")
    # The enum value is deliberately NOT removed: PostgreSQL cannot drop a
    # value from an enum type, and rows may already reference it.
