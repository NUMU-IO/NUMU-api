"""add promoted_item to marketing_campaigns

Revision ID: promoted_item_20260524
Revises: funnel_events_device_20260524
Create Date: 2026-05-24

Feature: "What is this campaign promoting?" — couples the hub's new
PromotedItemPicker to the campaign so the destination can drive both the
email body template (server-rendered later) and the Trackable-link panel
auto-prefill.

Stored as JSONB with snapshot semantics:

    {
      "kind": "product" | "collection" | "page",
      "ref_id": "<product_id | collection_slug | page_path>",
      "snapshot": {
        "name": "...",
        "image_url": "...",
        "price": "...",
        "currency": "EGP",
        "url": "https://<store>.numueg.app/..."
      }
    }

`snapshot` is cached at create / update time so a sent campaign's preview
always shows what the customer received, even if the underlying product
is renamed / repriced afterward. NULL means "this campaign isn't tied to
any specific item" (freeform broadcast).

No backfill — existing campaigns stay NULL, which the entity treats as
"no promoted item" everywhere it's consumed.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

from alembic import op

# revision identifiers.
revision: str = "promoted_item_20260524"
down_revision: str = "funnel_events_device_20260524"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "marketing_campaigns",
        sa.Column("promoted_item", JSONB, nullable=True),
        schema="public",
    )


def downgrade() -> None:
    op.drop_column("marketing_campaigns", "promoted_item", schema="public")
