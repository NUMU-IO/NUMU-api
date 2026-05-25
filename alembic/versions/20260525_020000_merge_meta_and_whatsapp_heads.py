"""merge meta and whatsapp migration heads

Revision ID: merge_meta_wa_heads_20260525
Revises: meta_custom_conversion_id_20260525, wa_optin_sched_dl_20260524
Create Date: 2026-05-25

Two parallel migration chains landed on dev simultaneously:

  * Meta integration: ``meta_custom_conversion_id_20260525`` (spec 005)
  * WhatsApp foundation: ``wa_optin_sched_dl_20260524`` (backend-030)

Each is its own self-contained chain — Meta's chain runs through
``promoted_item_20260524`` → ``funnel_events_device_20260524`` →
``campaign_activities_20260524`` etc., WhatsApp's chain starts at
``wa_optin_sched_dl_20260524`` and was authored against the same
parent (``funnel_events_device_20260524``).

`alembic upgrade head` errors out with multiple heads. This merge
migration declares both as parents, gives alembic a single new head
to target, and ships zero DDL of its own.

Refs the lesson from the alembic-sibling-branch-deploy-drift memory:
DO NOT rebase the two siblings into a single chain (that silently
skips one branch's DDL). Always merge.
"""

from collections.abc import Sequence

from alembic import op  # noqa: F401

# revision identifiers.
revision: str = "merge_meta_wa_heads_20260525"
down_revision: tuple[str, ...] = (
    "meta_custom_conversion_id_20260525",
    "wa_optin_sched_dl_20260524",
)
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    """No-op merge migration — just consolidates the two heads."""


def downgrade() -> None:
    """No-op — downgrading from a merge re-exposes both parents."""
