"""Key meta_event_log dedup on (store, pixel, event) instead of (store, event).

Meta deduplicates a repeated ``(pixel_id, event_name, event_id)`` within its
own 48-hour window, and that scope is **per Pixel ID**. Sending one event_id to
a store's second and third pixels is therefore correct and expected.

NUMU's log carried ``UNIQUE(store_id, event_id)``, which is strictly narrower
than Meta's rule. The fan-out loop in ``meta_capi_purchase_dispatcher`` enqueues
one task per capi-enabled pixel, and every task after the first hit the
constraint and was recorded as ``{"status": "duplicate"}`` — returning before
any HTTP call. A store with three pixels was, in practice, a store with one.

Adding ``pixel_id`` to the key fixes that. It also makes the identity-enrichment
resend path correct: the adopt-and-resend lookup now finds *this pixel's* row
rather than whichever pixel happened to log first.

Idempotent: drops the old constraint only if present, and skips creating the new
one if a prior partial run already made it.

Revision ID: meta_pixel_dedup_20260817
Revises: abandoned_phone_idx_20260814
Create Date: 2026-08-17
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "meta_pixel_dedup_20260817"
down_revision: str | Sequence[str] | None = "abandoned_phone_idx_20260814"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_OLD = "uq_meta_event_log_store_event_id"
# `ADD CONSTRAINT … USING INDEX` renames the index to the constraint name, so
# this is only the transient name during the concurrent build.
_INDEX = "uq_meta_event_log_store_pixel_event_id_idx"
_NEW = "uq_meta_event_log_store_pixel_event_id"
_TABLE = "meta_event_log"
_SCHEMA = "public"


def upgrade() -> None:
    # Build the index CONCURRENTLY, then adopt it as the constraint.
    #
    # `ADD CONSTRAINT … UNIQUE` builds its index while holding ACCESS
    # EXCLUSIVE, which blocks every INSERT into `meta_event_log` for the
    # duration — and this table has no retention policy, so it only ever
    # grows. On a small table that is a blink; on a large one it is a window
    # where the CAPI worker cannot record events. The size is not knowable
    # from here, so the migration is written to be safe at any size rather
    # than to bet on a number.
    #
    # `CREATE UNIQUE INDEX CONCURRENTLY` takes only SHARE UPDATE EXCLUSIVE
    # (writes continue), and `ADD CONSTRAINT … USING INDEX` then adopts it
    # with a momentary catalog lock instead of a full rebuild.
    #
    # CONCURRENTLY cannot run inside a transaction, hence the autocommit
    # block. That means this migration is not atomic: if it fails midway an
    # INVALID index can be left behind, which the pre-flight below cleans up
    # so a re-run converges instead of erroring.
    with op.get_context().autocommit_block():
        conn = op.get_bind()

        # A previous failed attempt leaves an INVALID index that would make
        # the CREATE below fail with "already exists". Drop it first.
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
        # `%(name)s`. `exec_driver_sql` passes the string to the DBAPI
        # verbatim, so psycopg2 paramstyle reaches asyncpg — which uses `$1`
        # — and Postgres rejects it with `syntax error at or near "%"`.
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

        # Drop the old, narrower constraint only AFTER the new one is live,
        # so there is never a window with no uniqueness guarantee at all —
        # the guarantee is what stops the CAPI worker double-sending.
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
