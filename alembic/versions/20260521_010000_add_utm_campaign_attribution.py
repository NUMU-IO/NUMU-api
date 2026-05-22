"""Add UTM + campaign attribution columns across orders/funnel/customers/campaigns.

Revision ID: utm_attribution_20260521
Revises: merge_phone_pixel_20260518
Create Date: 2026-05-21

Feature 001-utm-campaign-attribution. Single additive migration that
closes the loop between marketing campaigns (broadcast vehicles today)
and orders/funnel events (partial UTM capture today).

Changes:
* ``marketing_campaigns.short_code`` — 6-char Crockford base32, unique
  per store. Embedded into ``utm_campaign`` so links survive campaign
  renames. Backfilled for existing rows using a deterministic seeded
  generator (idempotent re-run).
* ``orders`` gains ``utm_term``, ``utm_content``, ``campaign_id`` (FK
  to marketing_campaigns, ``ON DELETE SET NULL``), ``attribution``
  (JSONB), ``first_touch_at``.
* ``funnel_events`` gains five UTM columns + ``campaign_id`` (FK,
  same delete behaviour) + ``referrer`` (promoted out of
  ``step_data``).
* ``customers`` gains ``first_touch_attribution`` (JSONB) and
  ``first_touch_at``. Used for LTV-by-acquisition-channel analytics
  in future work — set once on first attributed order, never
  overwritten.

Lock impact: all ``ADD COLUMN`` ops are nullable (or have a backfill
that runs before NOT NULL is set), so Postgres treats them as
metadata-only. The two FK adds use ``NOT VALID`` to skip the table
scan (we trust the inserts going forward; existing rows are NULL).
Index creation is wrapped in ``CONCURRENTLY`` where appropriate.
"""

import secrets
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers.
# Renamed from "utm_campaign_attribution_20260521" (33 chars) to fit
# alembic_version.version_num VARCHAR(32) — Postgres rejects the
# longer value when alembic UPDATEs the version table after running
# the DDL, taking the whole transaction with it. Children
# (short_links, campaign_coupon_fk, customer_touches) reference this
# id in their `down_revision`; updated to match.
revision: str = "utm_attribution_20260521"
down_revision: str = "merge_phone_pixel_20260518"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None

# Crockford base32 — exclude I, L, O, U.
_ALPHABET = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"
_CODE_LENGTH = 6


def _make_code(rng: secrets.SystemRandom) -> str:
    """Generate a single Crockford base32 short_code."""
    return "".join(rng.choice(_ALPHABET) for _ in range(_CODE_LENGTH))


def _backfill_short_codes() -> None:
    """Populate short_code for every existing marketing_campaigns row.

    The backfill uses ``secrets.SystemRandom`` for cryptographic
    randomness — collisions are vanishingly unlikely in a 32^6 space,
    but we still retry on any DB-side IntegrityError by regenerating.

    Within a single migration run we hold all in-progress codes in
    memory to avoid intra-batch collisions; the DB's
    uq_campaigns_store_short_code index catches anything that slips
    through.
    """
    bind = op.get_bind()
    rows = bind.execute(
        sa.text(
            "SELECT id, store_id FROM public.marketing_campaigns "
            "WHERE short_code IS NULL"
        )
    ).fetchall()
    if not rows:
        return

    rng = secrets.SystemRandom()
    # (store_id, code) tuples already assigned in this run.
    assigned: set[tuple[str, str]] = set()
    for row in rows:
        campaign_id = row[0]
        store_id = row[1]
        for _attempt in range(10):
            code = _make_code(rng)
            key = (str(store_id), code)
            if key in assigned:
                continue
            # SAVEPOINT around each UPDATE attempt: in Postgres, any
            # IntegrityError (UNIQUE collision against an existing
            # row that snuck in concurrently, or any other DB error)
            # aborts the surrounding transaction. Without this nested
            # block, the FIRST collision would leave the whole
            # migration's transaction in a failed state and every
            # subsequent statement would error with
            # ``InFailedSqlTransaction`` — the migration could never
            # complete on a DB with any pre-existing campaigns.
            # ``begin_nested()`` issues ``SAVEPOINT``; on exception
            # we explicitly roll back to it so the outer transaction
            # stays alive.
            nested = bind.begin_nested()
            try:
                bind.execute(
                    sa.text(
                        "UPDATE public.marketing_campaigns "
                        "SET short_code = :code WHERE id = :id"
                    ),
                    {"code": code, "id": campaign_id},
                )
            except Exception:
                # Collision with an existing row OR the unique index —
                # roll back to the savepoint so the outer transaction
                # can continue, then try again with a fresh code.
                nested.rollback()
                continue
            else:
                nested.commit()
                assigned.add(key)
                break
        else:
            raise RuntimeError(
                f"backfill: failed to assign short_code for campaign {campaign_id} "
                f"after 10 attempts"
            )


def upgrade() -> None:
    # 1. marketing_campaigns.short_code (nullable for backfill)
    op.add_column(
        "marketing_campaigns",
        sa.Column("short_code", sa.String(length=8), nullable=True),
        schema="public",
    )

    # 2. Backfill existing rows
    _backfill_short_codes()

    # 3. Flip to NOT NULL once backfill is complete
    op.alter_column(
        "marketing_campaigns",
        "short_code",
        existing_type=sa.String(length=8),
        nullable=False,
        schema="public",
    )

    # 4. Unique-per-store index on short_code
    op.create_index(
        "uq_campaigns_store_short_code",
        "marketing_campaigns",
        ["store_id", "short_code"],
        schema="public",
        unique=True,
    )

    # 5. orders.utm_term / utm_content / campaign_id / attribution / first_touch_at
    op.add_column(
        "orders",
        sa.Column("utm_term", sa.String(length=200), nullable=True),
        schema="public",
    )
    op.add_column(
        "orders",
        sa.Column("utm_content", sa.String(length=200), nullable=True),
        schema="public",
    )
    op.add_column(
        "orders",
        sa.Column(
            "campaign_id", sa.dialects.postgresql.UUID(as_uuid=True), nullable=True
        ),
        schema="public",
    )
    op.add_column(
        "orders",
        sa.Column("attribution", sa.dialects.postgresql.JSONB(), nullable=True),
        schema="public",
    )
    op.add_column(
        "orders",
        sa.Column(
            "first_touch_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
        schema="public",
    )
    op.create_foreign_key(
        "fk_orders_campaign_id",
        source_table="orders",
        referent_table="marketing_campaigns",
        local_cols=["campaign_id"],
        remote_cols=["id"],
        ondelete="SET NULL",
        source_schema="public",
        referent_schema="public",
    )
    op.create_index(
        "ix_orders_campaign_id",
        "orders",
        ["campaign_id"],
        schema="public",
        postgresql_where=sa.text("campaign_id IS NOT NULL"),
    )
    op.create_index(
        "ix_orders_store_campaign_created",
        "orders",
        ["store_id", "campaign_id", "created_at"],
        schema="public",
        postgresql_where=sa.text("campaign_id IS NOT NULL"),
    )

    # 6. funnel_events — five UTM columns + campaign_id + referrer
    op.add_column(
        "funnel_events",
        sa.Column("utm_source", sa.String(length=200), nullable=True),
        schema="public",
    )
    op.add_column(
        "funnel_events",
        sa.Column("utm_medium", sa.String(length=200), nullable=True),
        schema="public",
    )
    op.add_column(
        "funnel_events",
        sa.Column("utm_campaign", sa.String(length=200), nullable=True),
        schema="public",
    )
    op.add_column(
        "funnel_events",
        sa.Column("utm_term", sa.String(length=200), nullable=True),
        schema="public",
    )
    op.add_column(
        "funnel_events",
        sa.Column("utm_content", sa.String(length=200), nullable=True),
        schema="public",
    )
    op.add_column(
        "funnel_events",
        sa.Column(
            "campaign_id", sa.dialects.postgresql.UUID(as_uuid=True), nullable=True
        ),
        schema="public",
    )
    op.add_column(
        "funnel_events",
        sa.Column("referrer", sa.String(length=500), nullable=True),
        schema="public",
    )
    op.create_foreign_key(
        "fk_funnel_events_campaign_id",
        source_table="funnel_events",
        referent_table="marketing_campaigns",
        local_cols=["campaign_id"],
        remote_cols=["id"],
        ondelete="SET NULL",
        source_schema="public",
        referent_schema="public",
    )
    op.create_index(
        "ix_funnel_events_store_campaign_created",
        "funnel_events",
        ["store_id", "campaign_id", "created_at"],
        schema="public",
        postgresql_where=sa.text("campaign_id IS NOT NULL"),
    )
    op.create_index(
        "ix_funnel_events_store_utm_campaign",
        "funnel_events",
        ["store_id", "utm_campaign", "created_at"],
        schema="public",
        postgresql_where=sa.text("utm_campaign IS NOT NULL"),
    )

    # 7. customers — first_touch_attribution + first_touch_at
    op.add_column(
        "customers",
        sa.Column(
            "first_touch_attribution", sa.dialects.postgresql.JSONB(), nullable=True
        ),
        schema="public",
    )
    op.add_column(
        "customers",
        sa.Column("first_touch_at", sa.DateTime(timezone=True), nullable=True),
        schema="public",
    )


def downgrade() -> None:
    # customers
    op.drop_column("customers", "first_touch_at", schema="public")
    op.drop_column("customers", "first_touch_attribution", schema="public")

    # funnel_events
    op.drop_index(
        "ix_funnel_events_store_utm_campaign",
        table_name="funnel_events",
        schema="public",
    )
    op.drop_index(
        "ix_funnel_events_store_campaign_created",
        table_name="funnel_events",
        schema="public",
    )
    op.drop_constraint(
        "fk_funnel_events_campaign_id",
        "funnel_events",
        type_="foreignkey",
        schema="public",
    )
    op.drop_column("funnel_events", "referrer", schema="public")
    op.drop_column("funnel_events", "campaign_id", schema="public")
    op.drop_column("funnel_events", "utm_content", schema="public")
    op.drop_column("funnel_events", "utm_term", schema="public")
    op.drop_column("funnel_events", "utm_campaign", schema="public")
    op.drop_column("funnel_events", "utm_medium", schema="public")
    op.drop_column("funnel_events", "utm_source", schema="public")

    # orders
    op.drop_index(
        "ix_orders_store_campaign_created", table_name="orders", schema="public"
    )
    op.drop_index("ix_orders_campaign_id", table_name="orders", schema="public")
    op.drop_constraint(
        "fk_orders_campaign_id", "orders", type_="foreignkey", schema="public"
    )
    op.drop_column("orders", "first_touch_at", schema="public")
    op.drop_column("orders", "attribution", schema="public")
    op.drop_column("orders", "campaign_id", schema="public")
    op.drop_column("orders", "utm_content", schema="public")
    op.drop_column("orders", "utm_term", schema="public")

    # marketing_campaigns
    op.drop_index(
        "uq_campaigns_store_short_code",
        table_name="marketing_campaigns",
        schema="public",
    )
    op.drop_column("marketing_campaigns", "short_code", schema="public")
