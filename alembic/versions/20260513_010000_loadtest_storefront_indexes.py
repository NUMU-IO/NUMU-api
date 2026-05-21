"""Loadtest storefront indexes (Phase 5.9 remediation — Step 04).

Adds composite indexes to accelerate the storefront read paths the
2026-05-13 k6 baseline showed slow (PLP, PDP, category-tree nav):

    ix_products_store_active_created
        public.products (store_id, status, created_at DESC)
        WHERE status = 'ACTIVE'
        → PLP query:
            WHERE store_id = ? AND status = 'ACTIVE'
            ORDER BY created_at DESC

    ix_products_store_slug_active
        public.products (store_id, slug)
        WHERE status = 'ACTIVE'
        → PDP query:
            WHERE store_id = ? AND slug = ? AND status = 'ACTIVE'

    ix_categories_store_parent_active
        public.categories (store_id, parent_id)
        WHERE is_active = TRUE
        → Category-tree fetch / nav rendering.
        Plan §4.4 named the columns ``status`` and ``parent_category_id`` —
        the actual schema uses ``is_active`` (bool) and ``parent_id``.

Two plan candidates were dropped on inspection:

    * ix_products_search_vector (4.5) — already shipped in migration
      product_search_tsv_20260508.
    * ix_cart_items_* (4.6) — no cart_items table exists. The cart is
      Redis-backed (RedisCartRepository); see scripts/load/README.md.

EXPLAIN evidence:
    Not captured against a real database during authoring — no local
    Postgres available, and the staging load-test fixtures still need
    the Step 02 seeder run with real credentials before the table
    volumes would be representative. Justification rests on:
      * Column existence verified in
        src/infrastructure/database/models/tenant/product.py and
        src/infrastructure/database/models/tenant/category.py.
      * Query shapes derived from the storefront-public route
        handlers used by k6 (PLP / PDP / category nav).
      * No near-duplicate index already present (checked against
        existing ix_products_* / ix_categories_* via model and
        migration grep).
    Run EXPLAIN (ANALYZE, BUFFERS) on staging after applying and
    confirm the planner picks each new index before promoting to
    production. If pg_stat_user_indexes.idx_scan stays at 0 after
    24 h post-deploy, downgrade and re-evaluate.

All indexes are CREATEd CONCURRENTLY. CONCURRENTLY cannot run inside
a transaction; we ``COMMIT`` the implicit Alembic transaction first.

Revision ID: loadtest_idx_20260513
Revises: abandoned_checkouts_20260512
Create Date: 2026-05-13

Revision ID kept short (≤ 32 chars) because alembic_version.version_num
is VARCHAR(32) in this project; the longer ``loadtest_storefront_indexes_20260513``
would not fit.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "loadtest_idx_20260513"
down_revision: str | None = "abandoned_checkouts_20260512"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("COMMIT")

    op.execute(
        """
        CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_products_store_active_created
        ON public.products (store_id, status, created_at DESC)
        WHERE status = 'ACTIVE'
        """
    )

    op.execute(
        """
        CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_products_store_slug_active
        ON public.products (store_id, slug)
        WHERE status = 'ACTIVE'
        """
    )

    op.execute(
        """
        CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_categories_store_parent_active
        ON public.categories (store_id, parent_id)
        WHERE is_active = TRUE
        """
    )


def downgrade() -> None:
    op.execute("COMMIT")
    for name in (
        "ix_categories_store_parent_active",
        "ix_products_store_slug_active",
        "ix_products_store_active_created",
    ):
        op.execute(f"DROP INDEX CONCURRENTLY IF EXISTS public.{name}")
