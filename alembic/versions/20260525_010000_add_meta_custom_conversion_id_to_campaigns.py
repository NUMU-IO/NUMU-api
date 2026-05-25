"""add meta_custom_conversion_id to marketing_campaigns

Revision ID: meta_custom_conversion_id_20260525
Revises: promoted_item_20260524
Create Date: 2026-05-25

Spec 005 US6 v2 foundation. Caches the Meta Custom Conversion id that
NUMU auto-creates at campaign send time when Meta is connected.

Once populated, the hub's per-campaign Meta attribution card can hit
Meta's ``/insights`` endpoint scoped to this Custom Conversion to
surface real Last-touch / Assisted-touch numbers — no manual setup
required on the merchant's side.

NULL semantics:
  * Meta isn't connected on this store → stays NULL forever.
  * Meta is connected but the auto-create call failed → stays NULL;
    the next send retries.
  * Send hasn't happened yet → NULL until first send-now / sweep.

No backfill — existing campaigns keep NULL; the auto-creator runs on
new sends only.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers.
revision: str = "meta_custom_conversion_id_20260525"
down_revision: str = "promoted_item_20260524"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "marketing_campaigns",
        sa.Column("meta_custom_conversion_id", sa.String(length=64), nullable=True),
        schema="public",
    )


def downgrade() -> None:
    op.drop_column("marketing_campaigns", "meta_custom_conversion_id", schema="public")
