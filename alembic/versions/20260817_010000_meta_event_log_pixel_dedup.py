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

from alembic import op

revision: str = "meta_pixel_dedup_20260817"
down_revision: str | Sequence[str] | None = "abandoned_phone_idx_20260814"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_OLD = "uq_meta_event_log_store_event_id"
_NEW = "uq_meta_event_log_store_pixel_event_id"
_TABLE = "meta_event_log"
_SCHEMA = "public"


def upgrade() -> None:
    conn = op.get_bind()

    # Duplicate (store_id, pixel_id, event_id) triples cannot exist yet — the
    # old, narrower constraint made them impossible — so the new index can be
    # built without a dedupe pass.
    op.execute(f'ALTER TABLE {_SCHEMA}.{_TABLE} DROP CONSTRAINT IF EXISTS "{_OLD}"')

    exists = conn.exec_driver_sql(
        "SELECT 1 FROM pg_constraint WHERE conname = %(name)s",
        {"name": _NEW},
    ).scalar()
    if not exists:
        op.create_unique_constraint(
            _NEW, _TABLE, ["store_id", "pixel_id", "event_id"], schema=_SCHEMA
        )


def downgrade() -> None:
    conn = op.get_bind()

    op.execute(f'ALTER TABLE {_SCHEMA}.{_TABLE} DROP CONSTRAINT IF EXISTS "{_NEW}"')

    # Going back is only safe if no store ever fanned one event_id out to more
    # than one pixel while the wider key was in force. If any did, the narrow
    # constraint cannot be recreated — fail loudly rather than deleting a
    # merchant's conversion log to force the rollback through.
    dupes = conn.exec_driver_sql(
        f"""
        SELECT COUNT(*) FROM (
            SELECT store_id, event_id
            FROM {_SCHEMA}.{_TABLE}
            GROUP BY store_id, event_id
            HAVING COUNT(*) > 1
        ) d
        """
    ).scalar()
    if dupes:
        raise RuntimeError(
            f"Cannot restore {_OLD}: {dupes} (store_id, event_id) groups now span "
            "multiple pixels. Remove the extra pixels' rows first if this "
            "downgrade is genuinely intended."
        )

    op.create_unique_constraint(_OLD, _TABLE, ["store_id", "event_id"], schema=_SCHEMA)
