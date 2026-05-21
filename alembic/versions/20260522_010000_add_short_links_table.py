"""Add short_links table for the trackable-URL redirector.

Revision ID: short_links_20260522
Revises: utm_campaign_attribution_20260521
Create Date: 2026-05-22

Adds the ``short_links`` table behind ``numueg.app/r/{short_code}``. A
short link maps a globally-unique 8-char Crockford base32 code to a
pre-composed long URL (with UTMs already baked in) so merchants can
share a clean URL on print, QR, billboards, or anywhere a 200-char
campaign URL would be impractical.

Why a separate table (rather than reusing ``marketing_campaigns.short_code``):
* one campaign can have many trackable-link destinations (homepage,
  collection, multiple products) so a single short_code per campaign
  isn't enough.
* the redirector is a hot read path with no need for the join — a
  dedicated UNIQUE index on ``short_links.short_code`` keeps the
  lookup at one B-tree hop.

Why 8 chars (not 6 like ``marketing_campaigns.short_code``):
* the campaign code is per-store-unique, so 6 chars × 32 alphabet =
  ~1B per-store namespace is plenty.
* ``short_links.short_code`` is GLOBALLY unique across all stores, so
  the namespace is shared. 8 chars × 32 = ~1.1T entries makes
  collision probability negligible even at NUMU-wide scale and across
  many years of links.

Lock impact: pure ADD TABLE — no existing-row touches, no FKs validated
against historical data. Safe to run on a live primary without
maintenance windows.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers
revision: str = "short_links_20260522"
down_revision: str = "utm_campaign_attribution_20260521"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "short_links",
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
        # short_code is globally unique across all stores because the
        # redirector path /r/{code} has no store context. The DB UNIQUE
        # constraint backstops the generator's retry loop.
        sa.Column("short_code", sa.String(12), nullable=False),
        # destination_url is the pre-composed long URL (already includes
        # UTMs, the campaign suffix, and the store subdomain). The
        # redirector returns it verbatim in a 302; it does NOT
        # re-compose. Open-redirector defence happens at insert time
        # (the service validates the host against the store's
        # canonical origin), so by the time the row exists, the URL is
        # trusted.
        sa.Column("destination_url", sa.Text(), nullable=False),
        # Optional FK to the campaign that spawned this link. Nullable
        # because a merchant might generate a one-off trackable link
        # without a campaign wrapper. SET NULL on campaign delete so
        # the short link keeps redirecting (the URL still has UTMs).
        sa.Column(
            "campaign_id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            sa.ForeignKey("public.marketing_campaigns.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "click_count",
            sa.Integer(),
            nullable=False,
            server_default="0",
        ),
        sa.Column("last_clicked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "is_active",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("true"),
        ),
        # Optional expiry — for time-bound campaigns (Eid sale, flash
        # promo). NULL = never expires. The redirector treats both
        # is_active=false and expires_at<now as 404.
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        # Audit: which user created the link. Nullable for backfill /
        # system-generated links. NOT a FK to users because users
        # may be soft-deleted; we just need a paper trail.
        sa.Column(
            "created_by",
            sa.dialects.postgresql.UUID(as_uuid=True),
            nullable=True,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        schema="public",
    )

    # Hot lookup path — every /r/{code} hit. UNIQUE so the generator
    # collision retry has something to race against.
    op.create_index(
        "uq_short_links_short_code",
        "short_links",
        ["short_code"],
        unique=True,
        schema="public",
    )

    # Listing path: "all short links for store X, newest first".
    op.create_index(
        "ix_short_links_store_created",
        "short_links",
        ["store_id", sa.text("created_at DESC")],
        schema="public",
    )

    # Per-campaign filter for the campaign-detail page's "links spawned
    # by this campaign" view. Partial — most rows have a campaign_id;
    # the few standalone ones don't need to clutter this index.
    op.create_index(
        "ix_short_links_campaign_id",
        "short_links",
        ["campaign_id"],
        schema="public",
        postgresql_where=sa.text("campaign_id IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index(
        "ix_short_links_campaign_id", table_name="short_links", schema="public"
    )
    op.drop_index(
        "ix_short_links_store_created", table_name="short_links", schema="public"
    )
    op.drop_index(
        "uq_short_links_short_code", table_name="short_links", schema="public"
    )
    op.drop_table("short_links", schema="public")
