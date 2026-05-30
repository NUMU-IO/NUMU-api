"""Merge dev's two heads so the V3 marketplace chain has a single base.

Revision ID: merge_v3_base_20260724
Revises: is_internal_20260723, merge_meta_wa_heads_20260525
Create Date: 2026-07-24

`dev` carried two un-converged alembic heads:
  * ``is_internal_20260723``        — main branch tip
  * ``merge_meta_wa_heads_20260525`` — meta + whatsapp marketing branch tip

This is a no-op merge revision that converges them so the V3 theme
marketplace migrations (flags → snapshots → default theme → metadata)
chain onto a single head. Purely structural — no schema or data change.
"""

from collections.abc import Sequence

revision: str = "merge_v3_base_20260724"
down_revision: tuple[str, str] = (
    "is_internal_20260723",
    "merge_meta_wa_heads_20260525",
)
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    """No-op: structural merge of two existing heads."""
    pass


def downgrade() -> None:
    """No-op: splits back into the two prior heads."""
    pass
