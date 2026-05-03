"""Add theme_admin_config table.

Revision ID: theme_admin_config_20260503
Revises: analytics_indexes_20260501
Create Date: 2026-05-03

Stores per-theme global flags controlled by platform admins:
* ``is_visible`` — whether the theme appears in the merchant theme picker
* ``required_plan`` — minimum tenant plan required to activate it
* ``display_order`` — sort order in the merchant grid

Catalog of slugs lives in ``AVAILABLE_THEMES`` in the storefront route module;
we seed one row per known slug here. The admin GET endpoint also auto-upserts
missing slugs on read, so adding a new theme to ``AVAILABLE_THEMES`` doesn't
require a fresh migration.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "theme_admin_config_20260503"
down_revision: str | None = "analytics_indexes_20260501"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


# Snapshot of AVAILABLE_THEMES slugs at migration time. The admin route
# auto-upserts any newer slugs, so this list only needs to cover the seed.
SEED_SLUGS: list[str] = [
    "modern",
    "boutique",
    "elegant",
    "skeuomorphic",
    "tech-wave",
    "neo-brutalism",
    "editorial",
    "luxury-minimal",
    "empire",
    "kick-game",
    "street",
    "rabbitsocks",
    "gilded-glamour-boutique",
    "bazar",
    "saw-saw",
]


def upgrade() -> None:
    op.create_table(
        "theme_admin_config",
        sa.Column(
            "id",
            sa.Integer,
            primary_key=True,
            autoincrement=True,
        ),
        sa.Column("theme_slug", sa.String(80), nullable=False),
        sa.Column(
            "is_visible",
            sa.Boolean,
            nullable=False,
            server_default=sa.text("true"),
        ),
        sa.Column(
            "required_plan",
            sa.String(20),
            nullable=False,
            server_default="free",
        ),
        sa.Column(
            "display_order",
            sa.Integer,
            nullable=False,
            server_default="100",
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        schema="public",
    )

    op.create_index(
        "ix_theme_admin_config_theme_slug",
        "theme_admin_config",
        ["theme_slug"],
        unique=True,
        schema="public",
    )
    op.create_index(
        "ix_theme_admin_config_display_order",
        "theme_admin_config",
        ["display_order"],
        schema="public",
    )
    op.create_index(
        "ix_theme_admin_config_visible",
        "theme_admin_config",
        ["is_visible"],
        schema="public",
    )

    # Seed one row per known slug. Admin GET endpoint upserts the rest later.
    bind = op.get_bind()
    for index, slug in enumerate(SEED_SLUGS):
        bind.execute(
            sa.text(
                """
                INSERT INTO public.theme_admin_config
                    (theme_slug, is_visible, required_plan, display_order)
                VALUES
                    (:slug, true, 'free', :display_order)
                ON CONFLICT (theme_slug) DO NOTHING
                """
            ),
            {"slug": slug, "display_order": index * 10},
        )


def downgrade() -> None:
    op.drop_index(
        "ix_theme_admin_config_visible",
        table_name="theme_admin_config",
        schema="public",
    )
    op.drop_index(
        "ix_theme_admin_config_display_order",
        table_name="theme_admin_config",
        schema="public",
    )
    op.drop_index(
        "ix_theme_admin_config_theme_slug",
        table_name="theme_admin_config",
        schema="public",
    )
    op.drop_table("theme_admin_config", schema="public")
