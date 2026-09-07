"""Key tiktok_event_log dedup on (store, pixel, event) instead of (store, event).

The TikTok twin of ``meta_pixel_dedup_20260817``, and the same bug: TikTok
deduplicates a repeated ``(event, event_id)`` inside its own 48-hour window,
and that scope is **per Pixel Code**. Sending one ``event_id`` to a store's
second and third pixels is therefore correct and expected.

NUMU's log carried ``UNIQUE(store_id, event_id)``, which is strictly narrower
than TikTok's rule. Both fan-out loops — ``tiktok_capi_purchase_dispatcher``
and ``/track`` — enqueue one task per api-enabled pixel, and every task after
the first hit this constraint and returned ``{"status": "duplicate"}`` before
any HTTP call. A store with three pixels was, in practice, a store with one,
and nothing in the logs said so: a dropped pixel and a genuine repeat looked
identical.

Adding ``pixel_id`` to the key fixes that.

Idempotent: drops the old constraint only if present, and skips creating the
new one if a prior partial run already made it.

Revision ID: tiktok_pixel_dedup_20260908
Revises: intake_completion_20260902
Create Date: 2026-09-08
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "tiktok_pixel_dedup_20260908"
down_revision: str | Sequence[str] | None = "intake_completion_20260902"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_OLD = "uq_tiktok_event_log_store_event_id"
# `ADD CONSTRAINT … USING INDEX` renames the index to the constraint name, so
# this is only the transient name during the concurrent build.
_INDEX = "uq_tiktok_event_log_store_pixel_event_id_idx"
_NEW = "uq_tiktok_event_log_store_pixel_event_id"
_TABLE = "tiktok_event_log"
_SCHEMA = "public"


def upgrade() -> None:
    # Build the index CONCURRENTLY, then adopt it as the constraint — same
    # reasoning as the Meta migration: `ADD CONSTRAINT … UNIQUE` builds its
    # index under ACCESS EXCLUSIVE, blocking every INSERT into the table for
    # the duration, and this table has no retention policy so it only grows.
    # CONCURRENTLY takes SHARE UPDATE EXCLUSIVE instead, so the Events API
    # worker keeps recording while the index builds.
    #
    # CONCURRENTLY cannot run inside a transaction, hence the autocommit
    # block — which means this migration is not atomic. A failure midway
    # leaves an INVALID index, which the pre-flight below cleans up so a
    # re-run converges instead of erroring.
    with op.get_context().autocommit_block():
        conn = op.get_bind()

        invalid = conn.execute(
            sa.text("""
                SELECT 1
                FROM pg_class c
                JOIN pg_index i ON i.indexrelid = c.oid
                WHERE c.relname = :name AND NOT i.indisvalid
            """),
            {"name": _INDEX},
        ).scalar()
        if invalid:
            conn.execute(sa.text(f'DROP INDEX IF EXISTS {_SCHEMA}."{_INDEX}"'))

        # `sa.text()` with a `:name` bind, NOT `exec_driver_sql` with
        # `%(name)s` — asyncpg uses `$1` and rejects psycopg2 paramstyle with
        # `syntax error at or near "%"`.
        already = conn.execute(
            sa.text("SELECT 1 FROM pg_constraint WHERE conname = :name"),
            {"name": _NEW},
        ).scalar()
        if already:
            return

        conn.execute(
            sa.text(f"""
                CREATE UNIQUE INDEX CONCURRENTLY IF NOT EXISTS "{_INDEX}"
                ON {_SCHEMA}.{_TABLE} (store_id, pixel_id, event_id)
            """)
        )
        conn.execute(
            sa.text(f"""
                ALTER TABLE {_SCHEMA}.{_TABLE}
                ADD CONSTRAINT "{_NEW}" UNIQUE USING INDEX "{_INDEX}"
            """)
        )

        # Drop the old, narrower constraint only AFTER the new one is live, so
        # there is never a window with no uniqueness guarantee — that guarantee
        # is what stops the worker double-sending.
        conn.execute(
            sa.text(
                f'ALTER TABLE {_SCHEMA}.{_TABLE} DROP CONSTRAINT IF EXISTS "{_OLD}"'
            )
        )


def downgrade() -> None:
    conn = op.get_bind()

    op.execute(f'ALTER TABLE {_SCHEMA}.{_TABLE} DROP CONSTRAINT IF EXISTS "{_NEW}"')

    # Going back is only safe if no store ever fanned one event_id out to more
    # than one pixel while the wider key was in force. If any did, the narrow
    # constraint cannot be recreated — fail loudly rather than deleting a
    # merchant's conversion log to force the rollback through.
    dupes = conn.execute(
        sa.text(f"""
            SELECT COUNT(*) FROM (
                SELECT store_id, event_id
                FROM {_SCHEMA}.{_TABLE}
                GROUP BY store_id, event_id
                HAVING COUNT(*) > 1
            ) d
        """)
    ).scalar()
    if dupes:
        raise RuntimeError(
            f"Cannot restore {_OLD}: {dupes} (store_id, event_id) groups now span "
            "multiple pixels. Remove the extra pixels' rows first if this "
            "downgrade is genuinely intended."
        )

    op.create_unique_constraint(_OLD, _TABLE, ["store_id", "event_id"], schema=_SCHEMA)
