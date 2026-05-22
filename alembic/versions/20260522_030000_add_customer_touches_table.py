"""Add customer_touches table for full customer-journey timelines.

Revision ID: customer_touches_20260522
Revises: utm_campaign_attribution_20260521
Create Date: 2026-05-22

Stores every UTM-tagged inbound visit so the merchant can see the
**whole** path a customer took to conversion — not just first + last
touch (already captured on customers.first_touch_attribution +
orders.attribution).

This is the data foundation for multi-touch attribution models
(linear / time-decay / position-based) in a future feature. For v1
we only ship the storage + endpoint + timeline view; the model
switchers come later.

* Anonymous touches (no known customer yet) carry only
  ``session_fingerprint``. When the visitor converts at checkout, we
  UPDATE the matching rows to set ``customer_id``, linking the
  pre-auth history into the customer profile.
* Dedup is "different from the previous touch on this session" —
  refreshing a UTM-tagged page or noisy /track retries don't bloat
  the table. Implemented in the capture service, not at the index
  level (the dedup window is naturally bounded by session length).

Lock impact: pure ADD TABLE — no row touches, no historical-data FK
scans. Safe on a live primary.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers
revision: str = "customer_touches_20260522"
down_revision: str = "utm_attribution_20260521"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "customer_touches",
        sa.Column(
            "id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "store_id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            sa.ForeignKey("public.stores.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "tenant_id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            nullable=False,
        ),
        # NULL until the visitor's session converts; backfilled in a
        # single UPDATE at checkout. Lets us still record + display
        # anonymous browsing journeys via session_fingerprint.
        sa.Column(
            "customer_id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            sa.ForeignKey("public.customers.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("session_fingerprint", sa.String(128), nullable=False),
        sa.Column(
            "ts",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("utm_source", sa.String(200), nullable=True),
        sa.Column("utm_medium", sa.String(200), nullable=True),
        sa.Column("utm_campaign", sa.String(200), nullable=True),
        sa.Column("utm_term", sa.String(200), nullable=True),
        sa.Column("utm_content", sa.String(200), nullable=True),
        sa.Column("gclid", sa.String(256), nullable=True),
        sa.Column("fbclid", sa.String(256), nullable=True),
        sa.Column("referrer", sa.String(500), nullable=True),
        sa.Column("landing_path", sa.String(500), nullable=True),
        # FK to marketing_campaigns when the campaign_resolver matches
        # this touch's utm_campaign to a known campaign short_code.
        # NULL for un-resolvable / organic touches.
        sa.Column(
            "campaign_id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            sa.ForeignKey("public.marketing_campaigns.id", ondelete="SET NULL"),
            nullable=True,
        ),
        # Flag whether this is the customer's first ever touch (set at
        # capture time, never recomputed). Lets queries cheaply filter
        # to first-touch attribution without window functions.
        sa.Column(
            "is_first_touch",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
        schema="public",
    )

    # Hot read path: "show me this customer's journey".
    op.create_index(
        "ix_customer_touches_customer_ts",
        "customer_touches",
        ["customer_id", "ts"],
        schema="public",
        postgresql_where=sa.text("customer_id IS NOT NULL"),
    )
    # Pre-conversion lookup: "find this session's touches by
    # fingerprint" — used by the checkout backfill and by the
    # capture service's dedup check.
    op.create_index(
        "ix_customer_touches_session_ts",
        "customer_touches",
        ["session_fingerprint", "ts"],
        schema="public",
    )
    # Per-store admin filter (e.g. "all touches today across this
    # store" for a debug view); narrows the scan before the customer
    # / session filter.
    op.create_index(
        "ix_customer_touches_store_ts",
        "customer_touches",
        ["store_id", "ts"],
        schema="public",
    )
    # Campaign attribution lookups: "which touches led to this
    # campaign's known conversions" (partial, bounded by campaigns).
    op.create_index(
        "ix_customer_touches_campaign",
        "customer_touches",
        ["campaign_id"],
        schema="public",
        postgresql_where=sa.text("campaign_id IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index(
        "ix_customer_touches_campaign", table_name="customer_touches", schema="public"
    )
    op.drop_index(
        "ix_customer_touches_store_ts", table_name="customer_touches", schema="public"
    )
    op.drop_index(
        "ix_customer_touches_session_ts",
        table_name="customer_touches",
        schema="public",
    )
    op.drop_index(
        "ix_customer_touches_customer_ts",
        table_name="customer_touches",
        schema="public",
    )
    op.drop_table("customer_touches", schema="public")
