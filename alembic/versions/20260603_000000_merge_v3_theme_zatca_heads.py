"""Merge the V3 theme-engine and WhatsApp/Saudi-ZATCA migration heads.

Both branches descend from ``merge_v3_wa_heads_20260730``:

  - theme-engine line: add_menus_20260531 -> add_pages_20260601
                       -> add_theme_update_notifs_20260601
  - wa / saudi line:   ... -> saudi_gateways_20260602 -> zatca_invoice_20260602

That left the tree with TWO heads, so ``alembic upgrade head`` (singular) is
ambiguous and a deploy can silently follow only one branch. The test DB did
exactly that — it advanced down the ZATCA branch and never created the
theme-engine tables (menus / pages / marketplace_theme_update_notifications),
surfacing as ``UndefinedTableError`` on ``/stores/{id}/theme-updates/check``.

This is a pure merge revision — no DDL. It gives the tree a single head so
``alembic upgrade head`` applies BOTH branches on every env (incl. stage/prod
when PR #369 is promoted), preventing the same drift.

Revision ID: merge_v3_theme_zatca_20260603
Revises: add_theme_update_notifs_20260601, zatca_invoice_20260602
Create Date: 2026-06-03
"""

from collections.abc import Sequence

from alembic import op  # noqa: F401

revision: str = "merge_v3_theme_zatca_20260603"
down_revision: tuple[str, str] = (
    "add_theme_update_notifs_20260601",
    "zatca_invoice_20260602",
)
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    """Pure merge — no schema changes."""


def downgrade() -> None:
    """Pure merge — no schema changes."""
