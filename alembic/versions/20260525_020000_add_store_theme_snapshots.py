"""Add ``store_theme_snapshots`` for safe theme-switch rollback.

Revision ID: store_theme_snapshots_20260525
Revises: marketplace_flags_20260525
Create Date: 2026-05-25

Snapshot a merchant's existing customization before swapping themes
so they can revert with one click if the new theme breaks their
storefront. **Required protection for the V3 marketplace rollout** —
without this, activating a new theme is destructive (overwrites
customization_v3 + draft_customization_v3 unconditionally per the
existing dev-mode endpoint at themes.py:498-499).

Schema:
    id              UUID PK
    store_id        UUID FK → stores
    tenant_id       UUID (TenantMixin)
    theme_id        UUID FK → themes  (the theme the snapshot represents)
    theme_version_id UUID FK → theme_versions
    customization        JSONB (legacy V2 mirror)
    customization_v3     JSONB (V3 payload)
    reason          VARCHAR(60) (e.g. "pre-activation", "pre-upgrade")
    created_at      TIMESTAMPTZ
    restored_at     TIMESTAMPTZ NULL — set when this snapshot is used to revert

The snapshot is created by the install/activate service before any
write that would overwrite an active customization. Restoring is a
plain `store_themes.customization_v3 = snapshot.customization_v3`
under a transaction, plus updating `restored_at` for auditability.

No FK to ``store_themes(id)`` because we want snapshots to survive
uninstall — merchant can reinstall the old theme and we still find
the snapshot via (store_id, theme_id, created_at DESC).
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "store_theme_snapshots_20260525"
down_revision: str = "marketplace_flags_20260525"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "store_theme_snapshots",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            nullable=False,
        ),
        sa.Column(
            "store_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("stores.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "tenant_id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
        ),
        sa.Column(
            "theme_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("themes.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "theme_version_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("theme_versions.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "customization",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "customization_v3",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column("reason", sa.String(length=60), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "restored_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
    )

    # Per-store lookup, newest first — the "revert button" reads via this
    # to find the most recent snapshot for the currently-active theme.
    op.create_index(
        "ix_store_theme_snapshots_store_created",
        "store_theme_snapshots",
        ["store_id", sa.text("created_at DESC")],
    )

    # Per-tenant RLS (matches the pattern used by other tenant-scoped tables).
    op.create_index(
        "ix_store_theme_snapshots_tenant",
        "store_theme_snapshots",
        ["tenant_id"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_store_theme_snapshots_tenant",
        table_name="store_theme_snapshots",
    )
    op.drop_index(
        "ix_store_theme_snapshots_store_created",
        table_name="store_theme_snapshots",
    )
    op.drop_table("store_theme_snapshots")
