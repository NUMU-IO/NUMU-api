"""Add platform default theme reference.

Revision ID: platform_default_theme_20260527
Revises: store_theme_snapshots_20260525
Create Date: 2026-05-27

Adds a single ``default_marketplace_theme_id`` column to the
``platform_config`` key-value table. ``platform_config`` is a generic
``key/value JSONB`` store; the new column lives alongside the existing
``id``/``key``/``value`` columns. Only ONE row owns a non-NULL value —
the row keyed ``platform_default_theme`` (seeded lazily on first
admin-write). Every other row (``platform_settings``, ``meta_credentials``,
``plan_limits``, ``landing_page``) leaves the column NULL.

Why a real column instead of stuffing the UUID inside an existing
``value`` JSON blob:

  1. FK + ON DELETE SET NULL gives us automatic cleanup if a marketplace
     theme is hard-deleted (rare, but the audit trail says
     ``marketplace_themes`` can be soft-deleted today and a future hard-
     delete migration would otherwise leak a dangling default).
  2. The single-column index makes "what's the current default" a single
     B-tree lookup that joins to ``marketplace_themes`` cleanly.
  3. Migrating later to a dedicated singleton table is a column-move,
     not a JSON-path search-and-replace.

The migration is additive + nullable, so:
  - sawsaw + rabbit (production stores) are not touched. They were
    created before this code existed; their ``stores.theme_settings``
    is preserved. The platform default only fires for stores created
    AFTER an admin explicitly picks one.
  - Default is NULL → behaviour matches today (new stores fall through
    to the legacy V2 fallback). No deploy-time semantic change.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "platform_default_theme_20260527"
down_revision: str = "store_theme_snapshots_20260525"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "platform_config",
        sa.Column(
            "default_marketplace_theme_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey(
                "marketplace_themes.id",
                ondelete="SET NULL",
                name="fk_platform_config_default_marketplace_theme",
            ),
            nullable=True,
            comment=(
                "UUID of the marketplace_themes row used as the platform-wide "
                "default for newly-created stores. Only the row keyed "
                "'platform_default_theme' is expected to hold a non-NULL "
                "value; NULL on every row means 'no default, fall through "
                "to V2 legacy theme' (sawsaw/rabbit-safe)."
            ),
        ),
        schema="public",
    )

    # Partial-ish index — most rows are NULL so a regular index would be
    # mostly empty entries. The query that uses this column ("what's the
    # platform default") is keyed by ``key = 'platform_default_theme'``,
    # so the index pays off only when admins flip the value frequently;
    # still worth the few KB for sub-ms lookup.
    op.create_index(
        "ix_platform_config_default_theme",
        "platform_config",
        ["default_marketplace_theme_id"],
        schema="public",
    )


def downgrade() -> None:
    op.drop_index(
        "ix_platform_config_default_theme",
        table_name="platform_config",
        schema="public",
    )
    op.drop_constraint(
        "fk_platform_config_default_marketplace_theme",
        "platform_config",
        type_="foreignkey",
        schema="public",
    )
    op.drop_column(
        "platform_config",
        "default_marketplace_theme_id",
        schema="public",
    )
