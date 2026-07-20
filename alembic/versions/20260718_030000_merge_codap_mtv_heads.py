"""Merge heads: cod_ap_seed_20260718 + mtv_updated_at_20260715.

The 004-cod-autopilot chain was cut from ``platform_benchmarks_20260715``
while ``mtv_updated_at_20260715`` (marketplace version updated_at) sat on
a sibling branch, leaving two heads — ``alembic upgrade head`` then fails
with "Multiple head revisions". Per project convention
(alembic-sibling-branch-deploy-drift), a merge migration consolidates the
lines rather than rebasing deployed siblings.

No DDL — pure graph merge.

Revision ID: merge_codap_mtv_20260718
Revises: cod_ap_seed_20260718, mtv_updated_at_20260715
Create Date: 2026-07-18
"""

from collections.abc import Sequence

revision: str = "merge_codap_mtv_20260718"
down_revision: tuple[str, ...] = (
    "cod_ap_seed_20260718",
    "mtv_updated_at_20260715",
)
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
