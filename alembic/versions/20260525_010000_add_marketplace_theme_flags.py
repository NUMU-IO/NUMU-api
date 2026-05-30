"""Add `flags` JSONB to marketplace_themes for per-theme feature flags.

Revision ID: marketplace_flags_20260525
Revises: funnel_events_device_20260524
Create Date: 2026-05-25

Production-safe rollout gates for the V3 theme marketplace. The
catalog endpoint, install endpoint, and activate endpoint all read
this JSONB at request time to decide whether a theme is visible /
installable / activatable for the calling user.

Default ``{}`` means: theme is INVISIBLE in the public catalog. Existing
themes (including bon-younes from Phase 0 dev work) need explicit
backfill to remain visible — handled here by setting the bon-younes
slug to catalog_visible=true only because it was already in the
catalog from a prior DB patch on this single dev machine. **In real
production we leave the default empty and only flip flags via the
numu-admin UI after canary soak.**

Flag schema (validated in service layer, not in Postgres):
    {
        "catalog_visible": bool,
        "installable": bool,
        "activatable": bool,
        "visible_to_user_ids": [uuid-strings],
        "visible_to_pct": int (0-100)
    }
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "marketplace_flags_20260525"
down_revision: str = "merge_v3_base_20260724"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    # JSONB so the catalog query can filter on `flags->>'catalog_visible'`
    # without app-side join. Default '{}' so the column never reads NULL,
    # which simplifies the service-layer flag parser.
    op.add_column(
        "marketplace_themes",
        sa.Column(
            "flags",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
    )
    # GIN index — the catalog endpoint runs
    # `WHERE flags @> '{"catalog_visible": true}'` once per request.
    op.create_index(
        "ix_marketplace_themes_flags_gin",
        "marketplace_themes",
        ["flags"],
        postgresql_using="gin",
    )


def downgrade() -> None:
    op.drop_index(
        "ix_marketplace_themes_flags_gin",
        table_name="marketplace_themes",
    )
    op.drop_column("marketplace_themes", "flags")
