"""Lamaa app (ai-catalog): settings, bulk runs, suggestions, usage.

Four tenant-scoped tables with RLS, the shape of the other app tables. Nothing
writes them until a store installs the app (docs/Plans/APPS/04-ai-catalog).

Revision ID: ai_catalog_20261021
Revises: reviews_app_20261020
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "ai_catalog_20261021"
down_revision: str | Sequence[str] | None = "reviews_app_20261020"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

UUID = postgresql.UUID(as_uuid=True)
JSONB = postgresql.JSONB()
TABLES = (
    "ai_catalog_settings",
    "ai_catalog_bulk_runs",
    "ai_catalog_suggestions",
    "ai_catalog_usage",
)


def _stamps() -> list[sa.Column]:
    return [
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("NOW()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("NOW()"),
        ),
    ]


def _tenant() -> sa.Column:
    return sa.Column(
        "tenant_id",
        UUID,
        sa.ForeignKey("public.tenants.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )


def _store(**kw) -> sa.Column:
    return sa.Column(
        "store_id", UUID, sa.ForeignKey("public.stores.id", ondelete="CASCADE"), **kw
    )


def _id() -> sa.Column:
    return sa.Column(
        "id", UUID, primary_key=True, server_default=sa.text("gen_random_uuid()")
    )


def upgrade() -> None:
    op.create_table(
        "ai_catalog_settings",
        _store(primary_key=True),
        _tenant(),
        sa.Column("arabic_style", sa.String(8), nullable=False),
        sa.Column("languages", JSONB, nullable=False),
        sa.Column("store_facts", sa.Text(), nullable=False, server_default=""),
        sa.Column("brand_voice", sa.Text(), nullable=False, server_default=""),
        sa.Column("run_cap", sa.Integer(), nullable=True),
        *_stamps(),
        schema="public",
    )
    op.create_table(
        "ai_catalog_bulk_runs",
        _id(),
        _store(nullable=False),
        _tenant(),
        sa.Column("fields", JSONB, nullable=False),
        sa.Column("mode", sa.String(10), nullable=False),
        sa.Column("total", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("status", sa.String(10), nullable=False),
        sa.Column("created_by", UUID, nullable=True),
        *_stamps(),
        schema="public",
    )
    op.create_index(
        "ix_ai_catalog_bulk_runs_store_created",
        "ai_catalog_bulk_runs",
        ["store_id", "created_at"],
        schema="public",
    )
    op.create_table(
        "ai_catalog_suggestions",
        _id(),
        _store(nullable=False),
        sa.Column(
            "product_id",
            UUID,
            sa.ForeignKey("public.products.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "bulk_run_id",
            UUID,
            sa.ForeignKey("public.ai_catalog_bulk_runs.id", ondelete="CASCADE"),
            nullable=True,
        ),
        _tenant(),
        sa.Column("kind", sa.String(8), nullable=False),
        sa.Column("status", sa.String(14), nullable=False),
        sa.Column("fields", JSONB, nullable=False),
        sa.Column("claimed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("base_updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("current", JSONB, nullable=True),
        sa.Column("suggested", JSONB, nullable=True),
        sa.Column("applied", JSONB, nullable=True),
        sa.Column("applied_by", UUID, nullable=True),
        sa.Column("applied_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("model", sa.String(64), nullable=True),
        sa.Column("input_tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("output_tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("cost_micros", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("error_code", sa.String(32), nullable=True),
        *_stamps(),
        schema="public",
    )
    op.create_index(
        "ix_ai_catalog_suggestions_store_product",
        "ai_catalog_suggestions",
        ["store_id", "product_id"],
        schema="public",
    )
    op.create_index(
        "ix_ai_catalog_suggestions_run_status",
        "ai_catalog_suggestions",
        ["bulk_run_id", "status"],
        schema="public",
    )
    op.create_index(
        "ix_ai_catalog_suggestions_store_created",
        "ai_catalog_suggestions",
        ["store_id", "created_at"],
        schema="public",
    )
    op.create_table(
        "ai_catalog_usage",
        _store(primary_key=True),
        sa.Column("month", sa.String(7), primary_key=True),
        _tenant(),
        sa.Column("runs_used", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("cost_micros", sa.BigInteger(), nullable=False, server_default="0"),
        schema="public",
    )
    # Written out per table so scripts/check_migration_safety.py can read it.
    op.execute("ALTER TABLE public.ai_catalog_settings ENABLE ROW LEVEL SECURITY")
    op.execute(
        """
        CREATE POLICY tenant_isolation_ai_catalog_settings
        ON public.ai_catalog_settings
        USING (tenant_id = NULLIF(current_setting('app.current_tenant', true), '')::uuid)
        """
    )
    op.execute("ALTER TABLE public.ai_catalog_bulk_runs ENABLE ROW LEVEL SECURITY")
    op.execute(
        """
        CREATE POLICY tenant_isolation_ai_catalog_bulk_runs
        ON public.ai_catalog_bulk_runs
        USING (tenant_id = NULLIF(current_setting('app.current_tenant', true), '')::uuid)
        """
    )
    op.execute("ALTER TABLE public.ai_catalog_suggestions ENABLE ROW LEVEL SECURITY")
    op.execute(
        """
        CREATE POLICY tenant_isolation_ai_catalog_suggestions
        ON public.ai_catalog_suggestions
        USING (tenant_id = NULLIF(current_setting('app.current_tenant', true), '')::uuid)
        """
    )
    op.execute("ALTER TABLE public.ai_catalog_usage ENABLE ROW LEVEL SECURITY")
    op.execute(
        """
        CREATE POLICY tenant_isolation_ai_catalog_usage
        ON public.ai_catalog_usage
        USING (tenant_id = NULLIF(current_setting('app.current_tenant', true), '')::uuid)
        """
    )


def downgrade() -> None:
    for table in reversed(TABLES):
        op.execute(f"DROP POLICY IF EXISTS tenant_isolation_{table} ON public.{table}")
        op.drop_table(table, schema="public")
