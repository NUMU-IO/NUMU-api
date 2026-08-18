"""Give meta_event_log a delivery lifecycle so it can act as the CAPI outbox.

``meta_event_log`` already held everything an outbox needs — the hashed
payload, the attempt count, the last error, the per-(store, pixel, event)
uniqueness that is our dedup primitive. What it lacked was a *state*.

Without one, ``response_status IS NULL`` meant all of: queued, in flight,
worker crashed mid-send, and broker lost the message. So the platform could
not answer "how many events are we still owed", could not retry anything but
Purchase (which the orphan sweep rebuilds from ``orders``), and buried
everything else in a log line. This migration adds the four columns that
turn the log into a queue:

  status         where the event is in its life
  next_retry_at  when it may next be attempted, and the claim lease
  expires_at     the instant after which sending would DOUBLE-COUNT
  priority       so a Purchase is never stuck behind a PageView backlog

plus ``failure_kind``, which records *why* an attempt failed at the
granularity the retry decision uses — a dead token and a malformed payload
are both "4xx" and want very different responses from a merchant.

Existing rows default to ``legacy``
-----------------------------------
Deliberately not backfilled. ``ADD COLUMN … NOT NULL DEFAULT`` is a
metadata-only operation in PG11+, so this migration takes no table rewrite
and no long lock on a table the CAPI worker writes to on every event. A
backfill would buy nothing: every pre-existing row is far outside its 48h
dedup window, so none of them may be retried under any circumstances, and
``legacy`` states exactly that. Their outcome remains readable from
``response_status``, which is where the dashboards already read it.

During the deploy window (migration applied, old workers still running) the
old code inserts rows without a status and they land as ``legacy`` too. That
is harmless: the old path delivers synchronously and never consults status.

Revision ID: meta_outbox_lifecycle_20260818
Revises: meta_mq_snapshot_20260817
Create Date: 2026-08-18
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "meta_outbox_lifecycle_20260818"
down_revision: str | Sequence[str] | None = "meta_mq_snapshot_20260817"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLE = "meta_event_log"
_SCHEMA = "public"

# Claim/sweep index: the ORDER BY is (priority, next_retry_at) and the filter
# is a range on next_retry_at, so the composite in that order is seekable.
# Partial on the two open states keeps it a fraction of the table's size —
# the overwhelming majority of rows are terminal within seconds.
_IDX_DUE = "idx_meta_event_log_delivery_due"
# Per-store outstanding counts for the hub and the admin fleet view.
_IDX_OPEN = "idx_meta_event_log_store_open"

_OPEN_PREDICATE = "status IN ('pending', 'retrying')"


def upgrade() -> None:
    op.add_column(
        _TABLE,
        sa.Column(
            "status",
            sa.Text(),
            nullable=False,
            server_default="legacy",
        ),
        schema=_SCHEMA,
    )
    op.add_column(
        _TABLE,
        sa.Column("next_retry_at", sa.DateTime(timezone=True), nullable=True),
        schema=_SCHEMA,
    )
    op.add_column(
        _TABLE,
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        schema=_SCHEMA,
    )
    op.add_column(
        _TABLE,
        sa.Column(
            "priority",
            sa.SmallInteger(),
            nullable=False,
            server_default="1",
        ),
        schema=_SCHEMA,
    )
    op.add_column(
        _TABLE,
        sa.Column("failure_kind", sa.Text(), nullable=True),
        schema=_SCHEMA,
    )

    # CONCURRENTLY, for the same reason as the dedup-constraint migration:
    # a plain CREATE INDEX holds a lock that blocks every INSERT into this
    # table, and the CAPI worker inserts on every single event. Cannot run
    # inside a transaction, hence the autocommit block — which means the
    # migration is not atomic and a failed run can leave an INVALID index
    # behind, so each create is preceded by a cleanup that makes a re-run
    # converge rather than error.
    with op.get_context().autocommit_block():
        conn = op.get_bind()
        for name, columns, predicate in (
            (_IDX_DUE, "(priority, next_retry_at)", _OPEN_PREDICATE),
            (_IDX_OPEN, "(store_id, status)", _OPEN_PREDICATE),
        ):
            _drop_if_invalid(conn, name)
            conn.execute(
                sa.text(f"""
                    CREATE INDEX CONCURRENTLY IF NOT EXISTS "{name}"
                    ON {_SCHEMA}.{_TABLE} {columns}
                    WHERE {predicate}
                """)
            )


def _drop_if_invalid(conn: sa.engine.Connection, name: str) -> None:
    """Remove a half-built index left by a previous failed run.

    An INVALID index still occupies the name, so ``IF NOT EXISTS`` would skip
    the rebuild and leave the sweep querying without an index forever.
    """
    invalid = conn.execute(
        sa.text("""
            SELECT 1
            FROM pg_class c
            JOIN pg_index i ON i.indexrelid = c.oid
            WHERE c.relname = :name AND NOT i.indisvalid
        """),
        {"name": name},
    ).scalar()
    if invalid:
        conn.execute(sa.text(f'DROP INDEX IF EXISTS {_SCHEMA}."{name}"'))


def downgrade() -> None:
    with op.get_context().autocommit_block():
        conn = op.get_bind()
        for name in (_IDX_DUE, _IDX_OPEN):
            conn.execute(
                sa.text(f'DROP INDEX CONCURRENTLY IF EXISTS {_SCHEMA}."{name}"')
            )

    for column in (
        "failure_kind",
        "priority",
        "expires_at",
        "next_retry_at",
        "status",
    ):
        op.drop_column(_TABLE, column, schema=_SCHEMA)
