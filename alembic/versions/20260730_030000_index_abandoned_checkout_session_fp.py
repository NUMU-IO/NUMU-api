"""Index abandoned_checkouts by session fingerprint.

Two existing code paths look a cart up by ``extra_data->>'session_fingerprint'``:

  * ``AbandonedCheckoutRepository.find_active_for_session`` (checkout
    cart-state upsert — already shipped, already unindexed), and
  * ``_enrich_user_data_from_session`` in the storefront /track route, which
    resolves a GUEST's contact details for Meta/TikTok CAPI match quality.

A JSONB text extraction can't use any of the table's existing indexes
(store+abandoned_at, store+last_activity, email), so both did a scan of the
store's rows. That was tolerable for the cart-state write (once per checkout)
and is not for the CAPI path, which runs on every checkout-funnel event.

The index is an EXPRESSION index on (store_id, extra_data->>'session_fingerprint')
because every query filters both — store_id first so it also serves as a
prefix for store-only scans. Partial on a non-null fingerprint: rows written
before the fingerprint existed, and any future writer that omits it, are dead
weight in this index and are never searched for.

Created CONCURRENTLY so it does not take an ACCESS EXCLUSIVE lock on a table
that is written on the checkout path — a plain CREATE INDEX would block
checkout writes for the duration of the build.

Revision ID: abandoned_fp_idx_20260730
Revises: seo_overrides_20260729
Create Date: 2026-07-30
"""

from collections.abc import Sequence

from alembic import op

revision: str = "abandoned_fp_idx_20260730"
down_revision: str | None = "seo_overrides_20260729"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

INDEX_NAME = "idx_abandoned_checkouts_store_session_fp"


def upgrade() -> None:
    # CONCURRENTLY cannot run inside a transaction block, and Alembic wraps
    # migrations in one by default — commit it first, then build.
    op.execute("COMMIT")
    op.execute(
        f"""
        CREATE INDEX CONCURRENTLY IF NOT EXISTS {INDEX_NAME}
        ON public.abandoned_checkouts (
            store_id,
            (extra_data ->> 'session_fingerprint')
        )
        WHERE extra_data ->> 'session_fingerprint' IS NOT NULL
        """
    )


def downgrade() -> None:
    op.execute("COMMIT")
    op.execute(f"DROP INDEX CONCURRENTLY IF EXISTS public.{INDEX_NAME}")
