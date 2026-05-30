"""Merge the V3 marketplace and WhatsApp-defaults heads on dev.

Revision ID: merge_v3_wa_heads_20260730
Revises: marketplace_metadata_20260527, wa_notif_defaults_20260729
Create Date: 2026-07-30

`dev` carried two un-converged alembic heads:
  * ``marketplace_metadata_20260527`` — V3 theme marketplace chain tip
  * ``wa_notif_defaults_20260729``     — WhatsApp notification defaults backfill tip

This is a no-op merge revision that converges them so the deploy's
``alembic upgrade head`` resolves to a single head again. Purely
structural — no schema or data change.
"""

from collections.abc import Sequence

revision: str = "merge_v3_wa_heads_20260730"
down_revision: tuple[str, str] = (
    "marketplace_metadata_20260527",
    "wa_notif_defaults_20260729",
)
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    """No-op: structural merge of two existing heads."""
    pass


def downgrade() -> None:
    """No-op: splits back into the two prior heads."""
    pass
