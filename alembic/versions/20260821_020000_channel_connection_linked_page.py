"""Add linked_page_id to channel_connections.

Instagram messaging runs through the LINKED FACEBOOK PAGE node, not the
IG user node: POST /{page-id}/messages succeeds where
POST /{ig-user-id}/messages returns "(#3) Application does not have the
capability" (verified against the live Graph API 2026-08-21). The same
applies to the /conversations edge used for history backfill. The page
id is known at connect time — persist it so sends don't have to guess
which of a store's pages an IG account belongs to.

Idempotent: ADD COLUMN IF NOT EXISTS, safe to re-run.

Revision ID: chconn_linked_page_20260821
Revises: chmsg_updated_at_20260821
Create Date: 2026-08-21
"""

from alembic import op

# revision identifiers, used by Alembic.
revision = "chconn_linked_page_20260821"
down_revision = "chmsg_updated_at_20260821"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        ALTER TABLE public.channel_connections
        ADD COLUMN IF NOT EXISTS linked_page_id TEXT
        """
    )


def downgrade() -> None:
    op.execute(
        "ALTER TABLE public.channel_connections DROP COLUMN IF EXISTS linked_page_id"
    )
