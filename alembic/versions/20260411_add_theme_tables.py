"""Add theme engine tables.

Revision ID: d4e5f6a7b8c9
Revises: c1d2e3f40901
Create Date: 2026-04-11

Creates:
- public.themes          — global theme catalog
- public.theme_versions  — versioned bundles (immutable after upload)
- public.store_themes    — per-store theme installations (tenant-scoped)
- public.theme_assets    — static asset registry per version (used in Phase 3)

Enum types:
- themetype   ('internal', 'external')
- themestatus ('draft', 'published', 'suspended')

Constraints:
- themes.slug UNIQUE
- theme_versions(theme_id, version) UNIQUE
- store_themes partial UNIQUE INDEX on store_id WHERE is_active = true
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import ENUM, JSONB

from alembic import op

# revision identifiers
revision: str = "a4b5c6d7e8f9"
down_revision: str | None = "c1d2e3f40901"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # ── Enum types (created once, referenced with create_type=False on columns) ─
    theme_type_enum = ENUM(
        "internal",
        "external",
        name="themetype",
        schema="public",
        create_type=False,
    )
    theme_status_enum = ENUM(
        "draft",
        "published",
        "suspended",
        name="themestatus",
        schema="public",
        create_type=False,
    )
    op.execute(
        "DO $$ BEGIN CREATE TYPE public.themetype AS ENUM ('internal', 'external'); "
        "EXCEPTION WHEN duplicate_object THEN null; END $$;"
    )
    op.execute(
        "DO $$ BEGIN CREATE TYPE public.themestatus AS ENUM ('draft', 'published', 'suspended'); "
        "EXCEPTION WHEN duplicate_object THEN null; END $$;"
    )

    # ── public.themes ─────────────────────────────────────────────────────────
    op.create_table(
        "themes",
        sa.Column("id", sa.UUID(as_uuid=False), primary_key=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("slug", sa.String(255), nullable=False, unique=True),
        sa.Column("description", sa.Text, nullable=True),
        sa.Column("author", sa.String(255), nullable=False, server_default="NUMU"),
        sa.Column("type", theme_type_enum, nullable=False),
        sa.Column("thumbnail_url", sa.String(500), nullable=True),
        sa.Column("is_public", sa.Boolean, nullable=False, server_default="false"),
        sa.Column(
            "status",
            theme_status_enum,
            nullable=False,
            server_default="draft",
        ),
        sa.Column("settings_schema", JSONB, nullable=False, server_default="{}"),
        sa.Column("section_schemas", JSONB, nullable=True),
        sa.Column("supported_features", JSONB, nullable=True),
        sa.Column("created_by", sa.UUID(as_uuid=False), nullable=True),
        schema="public",
    )
    op.create_index("ix_themes_slug", "themes", ["slug"], unique=True, schema="public")

    # ── public.theme_versions ─────────────────────────────────────────────────
    op.create_table(
        "theme_versions",
        sa.Column("id", sa.UUID(as_uuid=False), primary_key=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "theme_id",
            sa.UUID(as_uuid=False),
            sa.ForeignKey("public.themes.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("version", sa.String(50), nullable=False),
        sa.Column("bundle_url", sa.String(500), nullable=False),
        sa.Column("css_url", sa.String(500), nullable=True),
        sa.Column("manifest", JSONB, nullable=False, server_default="{}"),
        sa.Column("changelog", sa.Text, nullable=True),
        sa.Column("is_latest", sa.Boolean, nullable=False, server_default="false"),
        sa.Column("size_bytes", sa.BigInteger, nullable=True),
        sa.Column("checksum", sa.String(64), nullable=False),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        schema="public",
    )
    op.create_index(
        "ix_theme_versions_theme_id",
        "theme_versions",
        ["theme_id"],
        schema="public",
    )
    op.create_unique_constraint(
        "uq_theme_version",
        "theme_versions",
        ["theme_id", "version"],
        schema="public",
    )

    # ── public.store_themes ───────────────────────────────────────────────────
    op.create_table(
        "store_themes",
        sa.Column("id", sa.UUID(as_uuid=False), primary_key=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "tenant_id",
            sa.UUID(as_uuid=False),
            sa.ForeignKey("public.tenants.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "store_id",
            sa.UUID(as_uuid=False),
            sa.ForeignKey("public.stores.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "theme_id",
            sa.UUID(as_uuid=False),
            sa.ForeignKey("public.themes.id"),
            nullable=False,
        ),
        sa.Column(
            "theme_version_id",
            sa.UUID(as_uuid=False),
            sa.ForeignKey("public.theme_versions.id"),
            nullable=False,
        ),
        sa.Column("is_active", sa.Boolean, nullable=False, server_default="false"),
        sa.Column("customization", JSONB, nullable=False, server_default="{}"),
        sa.Column("draft_customization", JSONB, nullable=False, server_default="{}"),
        sa.Column("installed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("activated_at", sa.DateTime(timezone=True), nullable=True),
        schema="public",
    )
    op.create_index(
        "ix_store_themes_store_id", "store_themes", ["store_id"], schema="public"
    )
    op.create_index(
        "ix_store_themes_tenant_id", "store_themes", ["tenant_id"], schema="public"
    )
    op.create_index(
        "ix_store_themes_is_active", "store_themes", ["is_active"], schema="public"
    )
    # Partial unique index — only one active theme per store
    op.execute(
        """
        CREATE UNIQUE INDEX ix_store_themes_active
        ON public.store_themes (store_id)
        WHERE is_active = true
        """
    )

    # ── public.theme_assets ───────────────────────────────────────────────────
    op.create_table(
        "theme_assets",
        sa.Column("id", sa.UUID(as_uuid=False), primary_key=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "theme_version_id",
            sa.UUID(as_uuid=False),
            sa.ForeignKey("public.theme_versions.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("file_path", sa.String(500), nullable=False),
        sa.Column("storage_key", sa.String(500), nullable=False),
        sa.Column("content_type", sa.String(100), nullable=False),
        sa.Column("size_bytes", sa.BigInteger, nullable=False),
        sa.Column("hash", sa.String(64), nullable=False),
        schema="public",
    )
    op.create_index(
        "ix_theme_assets_version_id",
        "theme_assets",
        ["theme_version_id"],
        schema="public",
    )


def downgrade() -> None:
    op.drop_table("theme_assets", schema="public")

    op.execute("DROP INDEX IF EXISTS public.ix_store_themes_active")
    op.drop_table("store_themes", schema="public")

    op.drop_table("theme_versions", schema="public")

    op.drop_table("themes", schema="public")

    op.execute("DROP TYPE IF EXISTS public.themestatus")
    op.execute("DROP TYPE IF EXISTS public.themetype")
