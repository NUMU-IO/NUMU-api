"""Merge meta_tracking, marketplace_rls, and promo_submit_event heads.

Revision ID: merge_heads_20260509
Revises: meta_tracking_20260427, marketplace_rls_20260507, promo_submit_event_20260508
Create Date: 2026-05-09

Three parallel migration chains landed on dev without a common descendant:

  * ``meta_tracking_20260427`` — Meta Pixel + CAPI foundation
  * ``marketplace_rls_20260507`` — RLS policies for marketplace user-scoped tables
  * ``promo_submit_event_20260508`` — adds ``submit`` to event_type_enum

`alembic upgrade head` fails with "Multiple head revisions are present".
This is a no-op merge node that joins all three branches so a single
``head`` exists again.
"""

from collections.abc import Sequence

revision: str = "merge_heads_20260509"
down_revision: tuple[str, str, str] = (
    "meta_tracking_20260427",
    "marketplace_rls_20260507",
    "promo_submit_event_20260508",
)
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """No schema changes — purely a graph-join."""
    pass


def downgrade() -> None:
    """No schema changes."""
    pass
