"""add device column to funnel_events

Revision ID: funnel_events_device_20260524
Revises: campaign_activities_20260524
Create Date: 2026-05-24

Feature 002 — marketing-campaigns-v2 (US3 "Sessions by device" panel).
Adds a nullable text column populated at ingest by the new
``device_classifier`` service (parses User-Agent → mobile/tablet/desktop).
Historical events stay NULL (surface as the "Unknown" donut bucket).
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers.
revision: str = "funnel_events_device_20260524"
down_revision: str = "campaign_activities_20260524"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "funnel_events",
        sa.Column("device", sa.String(length=16), nullable=True),
        schema="public",
    )
    op.create_check_constraint(
        "ck_funnel_events_device",
        "funnel_events",
        "device IN ('mobile', 'tablet', 'desktop') OR device IS NULL",
        schema="public",
    )
    # Drives the "Sessions by device" panel for a campaign. Partial
    # index on the non-null subset keeps it tiny + only useful for
    # campaign-scoped breakdowns.
    op.create_index(
        "ix_funnel_events_store_campaign_device",
        "funnel_events",
        ["store_id", "campaign_id", "device"],
        schema="public",
        postgresql_where=sa.text("device IS NOT NULL AND campaign_id IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index(
        "ix_funnel_events_store_campaign_device",
        table_name="funnel_events",
        schema="public",
    )
    op.drop_constraint(
        "ck_funnel_events_device",
        "funnel_events",
        type_="check",
        schema="public",
    )
    op.drop_column("funnel_events", "device", schema="public")
