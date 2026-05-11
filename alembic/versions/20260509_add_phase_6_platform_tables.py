"""Add Phase 6 platform tables — apps + app_installations + customizer_undo_entries + currency_rates.

Revision ID: phase_6_platform_20260509
Revises: merge_heads_20260509
Create Date: 2026-05-09

Phase 6 of the Shopify-parity audit. Four new tables, all in the
``public`` schema (RLS via ``tenant_id`` on the per-store ones):

* ``apps``                    — global registry of published apps.
* ``app_installations``       — per-store activation (tenant-scoped).
* ``customizer_undo_entries`` — server-side undo persistence
                                (replaces the client-side 50-FIFO).
* ``currency_rates``          — daily FX rates for multi-currency
                                presentment (display-only conversion).

No backfill: every existing store sees zero installs, zero undo
entries, and the currency_rates table starts empty (the daily Celery
task seeds it on first run; until then `<Money>` falls back to base
currency, which matches today's behavior).
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import ENUM, JSONB, UUID

from alembic import op

revision: str = "phase_6_platform_20260509"
down_revision: str | None = "merge_heads_20260509"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # ── apps (global registry) ─────────────────────────────────────
    app_status = ENUM(
        "draft", "published", "suspended", name="appstatus", create_type=False
    )
    app_status.create(op.get_bind(), checkfirst=True)

    op.create_table(
        "apps",
        sa.Column(
            "id",
            UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("slug", sa.String(64), nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("description", sa.String(2048), nullable=True),
        sa.Column(
            "developer_id",
            UUID(as_uuid=True),
            sa.ForeignKey("public.users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "status",
            app_status,
            nullable=False,
            server_default="draft",
        ),
        sa.Column("version", sa.String(32), nullable=False, server_default="0.1.0"),
        sa.Column("icon_url", sa.String(2048), nullable=True),
        sa.Column(
            "manifest",
            JSONB,
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
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
            onupdate=sa.func.now(),
            nullable=False,
        ),
        sa.UniqueConstraint("slug", name="uq_apps_slug"),
        schema="public",
    )
    op.create_index(
        "ix_apps_status",
        "apps",
        ["status"],
        unique=False,
        schema="public",
    )

    # ── app_installations (per-store) ──────────────────────────────
    op.create_table(
        "app_installations",
        sa.Column(
            "id",
            UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "tenant_id",
            UUID(as_uuid=True),
            sa.ForeignKey("public.tenants.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "store_id",
            UUID(as_uuid=True),
            sa.ForeignKey("public.stores.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "app_id",
            UUID(as_uuid=True),
            sa.ForeignKey("public.apps.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "is_enabled",
            sa.Boolean,
            nullable=False,
            server_default=sa.true(),
        ),
        sa.Column(
            "settings",
            JSONB,
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
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
            onupdate=sa.func.now(),
            nullable=False,
        ),
        sa.UniqueConstraint("store_id", "app_id", name="uq_app_installation_store_app"),
        schema="public",
    )
    op.create_index(
        "ix_app_installations_tenant",
        "app_installations",
        ["tenant_id"],
        unique=False,
        schema="public",
    )
    op.create_index(
        "ix_app_installations_store",
        "app_installations",
        ["store_id"],
        unique=False,
        schema="public",
    )
    # Hot path: list enabled installs for a store. Partial index
    # keeps the index tiny — most stores have few apps installed,
    # of which most are enabled.
    op.create_index(
        "ix_app_installations_enabled",
        "app_installations",
        ["store_id", "is_enabled"],
        unique=False,
        schema="public",
        postgresql_where=sa.text("is_enabled = true"),
    )

    # ── customizer_undo_entries ────────────────────────────────────
    op.create_table(
        "customizer_undo_entries",
        sa.Column(
            "id",
            UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "tenant_id",
            UUID(as_uuid=True),
            sa.ForeignKey("public.tenants.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "store_id",
            UUID(as_uuid=True),
            sa.ForeignKey("public.stores.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "user_id",
            UUID(as_uuid=True),
            sa.ForeignKey("public.users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("theme_id", sa.String(64), nullable=False),
        sa.Column("action_label", sa.String(128), nullable=False),
        sa.Column(
            "forward",
            JSONB,
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "inverse",
            JSONB,
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
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
            onupdate=sa.func.now(),
            nullable=False,
        ),
        schema="public",
    )
    op.create_index(
        "ix_customizer_undo_tenant",
        "customizer_undo_entries",
        ["tenant_id"],
        unique=False,
        schema="public",
    )
    # The user-scope index is the hot path: list/prune most-recent-first.
    op.create_index(
        "ix_customizer_undo_user_scope",
        "customizer_undo_entries",
        ["user_id", "store_id", "theme_id", "created_at"],
        unique=False,
        schema="public",
    )

    # ── currency_rates ─────────────────────────────────────────────
    op.create_table(
        "currency_rates",
        sa.Column("base", sa.String(3), nullable=False),
        sa.Column("target", sa.String(3), nullable=False),
        sa.Column("rate", sa.Numeric(18, 10), nullable=False),
        sa.Column(
            "fetched_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("base", "target", name="pk_currency_rates"),
        schema="public",
    )


def downgrade() -> None:
    op.drop_table("currency_rates", schema="public")

    op.drop_index(
        "ix_customizer_undo_user_scope",
        table_name="customizer_undo_entries",
        schema="public",
    )
    op.drop_index(
        "ix_customizer_undo_tenant",
        table_name="customizer_undo_entries",
        schema="public",
    )
    op.drop_table("customizer_undo_entries", schema="public")

    op.drop_index(
        "ix_app_installations_enabled",
        table_name="app_installations",
        schema="public",
    )
    op.drop_index(
        "ix_app_installations_store",
        table_name="app_installations",
        schema="public",
    )
    op.drop_index(
        "ix_app_installations_tenant",
        table_name="app_installations",
        schema="public",
    )
    op.drop_table("app_installations", schema="public")

    op.drop_index("ix_apps_status", table_name="apps", schema="public")
    op.drop_table("apps", schema="public")

    ENUM(name="appstatus").drop(op.get_bind(), checkfirst=True)
