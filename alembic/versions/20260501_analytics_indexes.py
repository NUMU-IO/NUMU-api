"""Add analytics-supporting indexes.

Revision ID: analytics_indexes_20260501
Revises: proof_block_reasons_20260430
Create Date: 2026-05-01

The analytics endpoints rewritten on 2026-04-30 push aggregation into
SQL — most of them filter on ``(store_id, created_at, status)`` and
group by columns or JSONB keys that aren't covered by the existing
single-column indexes. EXPLAIN ANALYZE on staging showed sequential
scans for /sales-by-location, /sessions, and the line_items unnest
queries.

All indexes are created ``CONCURRENTLY`` so the migration doesn't lock
writes on production. Alembic's autorun runs each step in its own
transaction (``op.execute`` with ``COMMIT``) because Postgres rejects
``CREATE INDEX CONCURRENTLY`` inside a transaction.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "analytics_indexes_20260501"
down_revision: str | None = "proof_block_reasons_20260430"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # CONCURRENTLY can't run inside a transaction. Commit the implicit
    # one Alembic wraps around us first.
    op.execute("COMMIT")

    op.execute(
        """
        CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_orders_store_created_status
        ON public.orders (store_id, created_at DESC, status)
        """
    )

    op.execute(
        """
        CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_orders_store_customer
        ON public.orders (store_id, customer_id)
        WHERE customer_id IS NOT NULL
        """
    )

    op.execute(
        """
        CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_orders_store_utm_source
        ON public.orders (store_id, lower(utm_source))
        WHERE utm_source IS NOT NULL
        """
    )

    op.execute(
        """
        CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_orders_shipping_city_gin
        ON public.orders USING gin ((shipping_address -> 'city'))
        """
    )

    op.execute(
        """
        CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_page_views_store_session
        ON public.page_views (store_id, session_fingerprint)
        WHERE session_fingerprint IS NOT NULL
        """
    )

    op.execute(
        """
        CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_funnel_events_step_data_gin
        ON public.funnel_events
        USING gin (step_data jsonb_path_ops)
        WHERE step_data IS NOT NULL
        """
    )

    op.execute(
        """
        CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_funnel_events_store_session
        ON public.funnel_events (store_id, session_fingerprint)
        WHERE session_fingerprint IS NOT NULL
        """
    )


def downgrade() -> None:
    op.execute("COMMIT")
    for name in (
        "ix_orders_store_created_status",
        "ix_orders_store_customer",
        "ix_orders_store_utm_source",
        "ix_orders_shipping_city_gin",
        "ix_page_views_store_session",
        "ix_funnel_events_step_data_gin",
        "ix_funnel_events_store_session",
    ):
        op.execute(f"DROP INDEX CONCURRENTLY IF EXISTS public.{name}")
