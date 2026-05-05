"""Add V3 theme columns, version history, marketplace, and installations.

Revision ID: v3_theme_001
Revises: activate_tenants_broad_20260419
Create Date: 2026-04-20

ADDITIVE ONLY — no existing columns are modified or dropped.

Adds:
- store_themes.customization_v3 / draft_customization_v3 (JSONB)
- stores.use_nextjs_storefront (Boolean) — per-store rollout flag
- theme_customization_versions — V3 autosave history (capped, see service)
- marketplace_themes / marketplace_theme_versions — marketplace catalog
- marketplace_theme_installations — per-store install tracking
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB, UUID

from alembic import op

revision: str = "v3_theme_001"
down_revision: str | None = "activate_tenants_broad_20260419"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # 1. Add V3 columns to store_themes (additive only)
    op.add_column(
        "store_themes",
        sa.Column("customization_v3", JSONB, server_default="{}", nullable=False),
        schema="public",
    )
    op.add_column(
        "store_themes",
        sa.Column("draft_customization_v3", JSONB, server_default="{}", nullable=False),
        schema="public",
    )

    # 2. Add feature flag to stores (additive only)
    op.add_column(
        "stores",
        sa.Column(
            "use_nextjs_storefront",
            sa.Boolean,
            server_default="false",
            nullable=False,
        ),
        schema="public",
    )

    # 3. Create version history table
    op.create_table(
        "theme_customization_versions",
        sa.Column(
            "id",
            UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "store_id",
            UUID(as_uuid=True),
            sa.ForeignKey("public.stores.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("theme_id", sa.String(255), nullable=False),
        sa.Column("settings_blob", JSONB, nullable=False),
        sa.Column("change_summary", sa.String(500), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("NOW()"),
            nullable=False,
        ),
        sa.Column("created_by", UUID(as_uuid=True), nullable=True),
        sa.Column("is_published", sa.Boolean, nullable=False, server_default="false"),
        sa.Column("is_autosave", sa.Boolean, nullable=False, server_default="true"),
        sa.Column("version_label", sa.String(255), nullable=True),
        schema="public",
    )
    op.create_index(
        "idx_tcv_store_id",
        "theme_customization_versions",
        ["store_id"],
        schema="public",
    )
    op.create_index(
        "idx_tcv_store_created",
        "theme_customization_versions",
        ["store_id", sa.text("created_at DESC")],
        schema="public",
    )

    # 4. Create marketplace tables
    op.create_table(
        "marketplace_themes",
        sa.Column(
            "id",
            UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "developer_id",
            UUID(as_uuid=True),
            sa.ForeignKey("public.users.id"),
            nullable=False,
        ),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("slug", sa.String(255), unique=True, nullable=False),
        sa.Column("description", sa.Text, nullable=True),
        sa.Column("short_description", sa.String(500), nullable=True),
        sa.Column("price_cents", sa.Integer, server_default="0", nullable=False),
        sa.Column("currency", sa.String(10), server_default="USD", nullable=False),
        sa.Column("status", sa.String(50), server_default="draft", nullable=False),
        sa.Column("thumbnail_url", sa.String(1024), nullable=True),
        sa.Column("preview_url", sa.String(1024), nullable=True),
        sa.Column("demo_store_url", sa.String(1024), nullable=True),
        sa.Column("tags", JSONB, server_default="[]", nullable=False),
        sa.Column("category", sa.String(100), nullable=True),
        sa.Column(
            "supported_languages",
            JSONB,
            server_default='["en","ar"]',
            nullable=False,
        ),
        sa.Column("supported_features", JSONB, server_default="{}", nullable=False),
        sa.Column("install_count", sa.Integer, server_default="0", nullable=False),
        sa.Column("average_rating", sa.Float, server_default="0.0", nullable=False),
        sa.Column("review_count", sa.Integer, server_default="0", nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("NOW()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("NOW()"),
            nullable=False,
        ),
        schema="public",
    )
    op.create_index(
        "idx_marketplace_themes_status",
        "marketplace_themes",
        ["status"],
        schema="public",
    )
    op.create_index(
        "idx_marketplace_themes_developer",
        "marketplace_themes",
        ["developer_id"],
        schema="public",
    )

    op.create_table(
        "marketplace_theme_versions",
        sa.Column(
            "id",
            UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "theme_id",
            UUID(as_uuid=True),
            sa.ForeignKey("public.marketplace_themes.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("version_string", sa.String(50), nullable=False),
        sa.Column("bundle_url", sa.String(1024), nullable=True),
        sa.Column("css_url", sa.String(1024), nullable=True),
        sa.Column("settings_schema", JSONB, server_default="{}", nullable=False),
        sa.Column("section_schemas", JSONB, server_default="{}", nullable=False),
        sa.Column("presets", JSONB, server_default="{}", nullable=False),
        sa.Column("release_notes", sa.Text, nullable=True),
        sa.Column(
            "status", sa.String(50), server_default="pending_build", nullable=False
        ),
        sa.Column("build_log", sa.Text, nullable=True),
        sa.Column("size_bytes", sa.BigInteger, nullable=True),
        sa.Column("checksum", sa.String(64), nullable=True),
        sa.Column("source_zip_path", sa.String(1024), nullable=True),
        sa.Column("review_notes", sa.Text, nullable=True),
        sa.Column("reviewed_by", UUID(as_uuid=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("NOW()"),
            nullable=False,
        ),
        sa.UniqueConstraint("theme_id", "version_string", name="uq_mtv_theme_version"),
        schema="public",
    )
    op.create_index(
        "idx_mtv_status",
        "marketplace_theme_versions",
        ["status"],
        schema="public",
    )

    # 5. Marketplace install tracking — separate from store_themes so we can
    #    cleanly track uninstalls and per-version installs.
    op.create_table(
        "marketplace_theme_installations",
        sa.Column(
            "id",
            UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "store_id",
            UUID(as_uuid=True),
            sa.ForeignKey("public.stores.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "marketplace_theme_id",
            UUID(as_uuid=True),
            sa.ForeignKey("public.marketplace_themes.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "marketplace_version_id",
            UUID(as_uuid=True),
            sa.ForeignKey("public.marketplace_theme_versions.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("is_active", sa.Boolean, nullable=False, server_default="false"),
        sa.Column(
            "installed_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("NOW()"),
            nullable=False,
        ),
        sa.Column("uninstalled_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint(
            "store_id", "marketplace_theme_id", name="uq_mti_store_theme"
        ),
        schema="public",
    )
    op.create_index(
        "idx_mti_store",
        "marketplace_theme_installations",
        ["store_id"],
        schema="public",
    )


def downgrade() -> None:
    op.drop_index("idx_mti_store", "marketplace_theme_installations", schema="public")
    op.drop_table("marketplace_theme_installations", schema="public")
    op.drop_index("idx_mtv_status", "marketplace_theme_versions", schema="public")
    op.drop_table("marketplace_theme_versions", schema="public")
    op.drop_index(
        "idx_marketplace_themes_developer", "marketplace_themes", schema="public"
    )
    op.drop_index(
        "idx_marketplace_themes_status", "marketplace_themes", schema="public"
    )
    op.drop_table("marketplace_themes", schema="public")
    op.drop_index(
        "idx_tcv_store_created", "theme_customization_versions", schema="public"
    )
    op.drop_index("idx_tcv_store_id", "theme_customization_versions", schema="public")
    op.drop_table("theme_customization_versions", schema="public")
    op.drop_column("stores", "use_nextjs_storefront", schema="public")
    op.drop_column("store_themes", "draft_customization_v3", schema="public")
    op.drop_column("store_themes", "customization_v3", schema="public")
