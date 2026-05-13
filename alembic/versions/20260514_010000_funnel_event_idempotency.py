"""Add event_id idempotency key to funnel_events (Step 09 — async tracking).

Step 09 moves funnel-event writes off the request path onto a Celery
queue. To stay correct under worker retries / acks-late redelivery,
each event carries a client-provided ``event_id`` UUID; the Celery
task inserts with ``ON CONFLICT (event_id) DO NOTHING`` so a duplicate
delivery is a no-op.

Schema shape:
  * ``funnel_events.event_id UUID NULL`` — nullable so legacy rows
    written before this column existed (and the rare sync fallback when
    ``analytics_async_enabled`` is False and the caller doesn't supply
    one) stay valid.
  * Partial UNIQUE INDEX ``ux_funnel_events_event_id`` on
    ``(event_id) WHERE event_id IS NOT NULL`` — Postgres treats every
    NULL as distinct, but a partial index makes the intent explicit
    and skips the legacy rows entirely.

Both DDL statements run with ``CONCURRENTLY`` so a busy
``funnel_events`` table (which can be millions of rows) doesn't block
writes while the index builds.

Revision ID: funnel_event_idemp_20260514
Revises: loadtest_idx_20260513
Create Date: 2026-05-14
"""

from collections.abc import Sequence

from alembic import op

revision: str = "funnel_event_idemp_20260514"
down_revision: str | None = "loadtest_idx_20260513"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Add the column via plain ALTER. Adding a NULLable column without a
    # default is a metadata-only change on Postgres — no table rewrite.
    op.execute(
        """
        ALTER TABLE public.funnel_events
        ADD COLUMN IF NOT EXISTS event_id UUID
        """
    )

    # CONCURRENTLY can't run inside a transaction. Commit the implicit
    # one Alembic wraps around us first.
    op.execute("COMMIT")
    op.execute(
        """
        CREATE UNIQUE INDEX CONCURRENTLY IF NOT EXISTS ux_funnel_events_event_id
        ON public.funnel_events (event_id)
        WHERE event_id IS NOT NULL
        """
    )


def downgrade() -> None:
    op.execute("COMMIT")
    op.execute("DROP INDEX CONCURRENTLY IF EXISTS public.ux_funnel_events_event_id")
    op.execute("ALTER TABLE public.funnel_events DROP COLUMN IF EXISTS event_id")
