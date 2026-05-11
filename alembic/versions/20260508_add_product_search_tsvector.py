"""Add tsvector search column + GIN index to products (Phase 4.1).

Revision ID: product_search_tsv_20260508
Revises: coupon_phase_3_8_20260508
Create Date: 2026-05-08

The storefront's `useSearch` hook (shipped in Phase 2) talks to
`/api/storefront/search?q=…`. Until now the search endpoint either
404'd or fell through to a `LIKE %q%` query — quadratic in the
catalog size and unable to rank by relevance.

This migration adds a Postgres tsvector generated column on `products`
and a GIN index on it. The column re-computes from
  setweight(to_tsvector('simple', name), 'A') ||
  setweight(to_tsvector('simple', sku   ), 'B') ||
  setweight(to_tsvector('simple', description), 'C') ||
  setweight(to_tsvector('simple', array_to_string(tags, ' ')), 'D')
on every insert/update via a STORED generated column. The 'simple'
text-search config is intentionally unaccented + non-language-specific
so Arabic and English queries match on the same column without
splitting into per-language vectors. Stemming is the cost; for v1's
storefront search ("hoodie" matching "hoodie black"), word-prefix
matching via `to_tsquery('foo:*')` covers the gap.

Why STORED instead of a trigger:
    Postgres 12+ supports `GENERATED ALWAYS AS (…) STORED` natively.
    Triggers add per-row overhead and are an extra dependency surface
    when restoring from backup. Generated columns are part of the
    table definition — restore-and-go.

GIN index parameters:
    `gin_trgm_ops` would add fuzzy-match support (e.g. "shrt" → "shirt")
    but requires the pg_trgm extension and inflates the index. Defer
    to Phase 5 when we wire trigram fuzzy fallback.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "product_search_tsv_20260508"
down_revision: str | None = "coupon_phase_3_8_20260508"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


# Single tsvector built from name (weight A) + sku (B) + description (C) +
# tags (D). 'simple' config = no stemming, no stop-word removal — good for
# product catalogs where exact-token match matters more than NLP smarts.
#
# Postgres rejects the built-in ``array_to_string(text[], text)`` inside a
# ``GENERATED ALWAYS AS … STORED`` expression — although the function is
# nominally IMMUTABLE in the catalog, the planner's volatility check for a
# stored generation expression treats it as not-strictly-immutable because
# the output depends on the element type's per-cluster output function. The
# workaround is a thin SQL wrapper marked ``IMMUTABLE`` ourselves, scoped
# narrowly to the ``text[]`` overload so we don't hide volatility on other
# array types.
SEARCH_VECTOR_EXPR = (
    "setweight(to_tsvector('simple', coalesce(name, '')), 'A') || "
    "setweight(to_tsvector('simple', coalesce(sku, '')), 'B') || "
    "setweight(to_tsvector('simple', coalesce(description, '')), 'C') || "
    "setweight(to_tsvector('simple', coalesce(public.immutable_text_array_to_string(tags, ' '), '')), 'D')"
)


def upgrade() -> None:
    # Immutable wrapper for ``array_to_string(text[], text)`` — required so the
    # STORED generated expression below passes Postgres's immutability check.
    op.execute(
        """
        CREATE OR REPLACE FUNCTION public.immutable_text_array_to_string(text[], text)
        RETURNS text
        LANGUAGE sql
        IMMUTABLE
        PARALLEL SAFE
        AS $$ SELECT array_to_string($1, $2) $$
        """
    )

    # Generated columns require explicit DDL since Alembic's
    # add_column doesn't yet have a clean wrapper for it. We drop down
    # to raw SQL.
    op.execute(
        f"""
        ALTER TABLE public.products
        ADD COLUMN search_vector tsvector
            GENERATED ALWAYS AS ({SEARCH_VECTOR_EXPR}) STORED
        """
    )

    # GIN index for full-text + prefix-match lookups. Concurrent build
    # would be safer for live tables but Alembic doesn't run inside a
    # transaction-friendly path for CONCURRENTLY — we accept the brief
    # write lock during this migration.
    op.create_index(
        "ix_products_search_vector",
        "products",
        ["search_vector"],
        unique=False,
        schema="public",
        postgresql_using="gin",
    )


def downgrade() -> None:
    op.drop_index(
        "ix_products_search_vector",
        table_name="products",
        schema="public",
    )
    op.drop_column("products", "search_vector", schema="public")
    op.execute(
        "DROP FUNCTION IF EXISTS public.immutable_text_array_to_string(text[], text)"
    )
