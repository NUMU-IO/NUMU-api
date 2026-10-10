"""Back in Stock app: back_in_stock_settings and back_in_stock_waiters.

The app's own tenant tables (docs/Plans/APPS/01-back-in-stock, data model).
Nothing reads or writes them until a store installs the app. RLS on, the same
shape as every other tenant-scoped table.

Revision ID: back_in_stock_20261009
Revises: strip_plain_store_pw_20261001
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "back_in_stock_20261009"
down_revision: str | Sequence[str] | None = "strip_plain_store_pw_20261001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

UUID = postgresql.UUID(as_uuid=True)


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


def upgrade() -> None:
    op.create_table(
        "back_in_stock_settings",
        _store(primary_key=True),
        _tenant(),
        sa.Column("contact", sa.String(20), nullable=False),
        sa.Column("signup_cap", sa.Integer(), nullable=False),
        sa.Column("wa_cap", sa.Integer(), nullable=False),
        sa.Column("email_cap", sa.Integer(), nullable=False),
        *_stamps(),
        schema="public",
    )
    op.create_table(
        "back_in_stock_waiters",
        sa.Column(
            "id", UUID, primary_key=True, server_default=sa.text("gen_random_uuid()")
        ),
        _store(nullable=False),
        _tenant(),
        sa.Column("product_id", UUID, nullable=False),
        sa.Column("variant_id", UUID, nullable=True),
        sa.Column("channel", sa.String(10), nullable=False),
        sa.Column("contact", sa.String(254), nullable=True),
        sa.Column("locale", sa.String(5), nullable=False, server_default="ar"),
        sa.Column("status", sa.String(15), nullable=False, server_default="waiting"),
        sa.Column("product_title", sa.String(255), nullable=False),
        sa.Column("variant_title", sa.String(255), nullable=True),
        sa.Column("queued_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("notified_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("message_id", sa.String(255), nullable=True),
        sa.Column("fail_reason", sa.String(64), nullable=True),
        sa.Column("link_token", sa.String(32), nullable=False, unique=True),
        sa.Column("unsub_token", sa.String(32), nullable=False, unique=True),
        sa.Column("clicked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("order_id", UUID, nullable=True),
        sa.Column("purchased_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revenue", sa.Integer(), nullable=True),
        *_stamps(),
        schema="public",
    )
    op.create_index(
        "ix_bis_waiters_store_product_status",
        "back_in_stock_waiters",
        ["store_id", "product_id", "status"],
        schema="public",
    )
    # One waiting row per (store, product, variant, contact); a NULL variant
    # counts as a value. coalesce() instead of NULLS NOT DISTINCT, which needs
    # Postgres 15.
    op.execute(
        """
        CREATE UNIQUE INDEX uq_bis_waiters_one_waiting
        ON public.back_in_stock_waiters (
            store_id,
            product_id,
            coalesce(variant_id, '00000000-0000-0000-0000-000000000000'::uuid),
            contact
        )
        WHERE status = 'waiting'
        """
    )
    # Written out per table so scripts/check_migration_safety.py can read it.
    op.execute("ALTER TABLE public.back_in_stock_settings ENABLE ROW LEVEL SECURITY")
    op.execute(
        """
        CREATE POLICY tenant_isolation_back_in_stock_settings
        ON public.back_in_stock_settings
        USING (tenant_id = NULLIF(current_setting('app.current_tenant', true), '')::uuid)
        """
    )
    op.execute("ALTER TABLE public.back_in_stock_waiters ENABLE ROW LEVEL SECURITY")
    op.execute(
        """
        CREATE POLICY tenant_isolation_back_in_stock_waiters
        ON public.back_in_stock_waiters
        USING (tenant_id = NULLIF(current_setting('app.current_tenant', true), '')::uuid)
        """
    )


def downgrade() -> None:
    op.execute(
        "DROP POLICY IF EXISTS tenant_isolation_back_in_stock_waiters "
        "ON public.back_in_stock_waiters"
    )
    op.execute(
        "DROP POLICY IF EXISTS tenant_isolation_back_in_stock_settings "
        "ON public.back_in_stock_settings"
    )
    op.drop_table("back_in_stock_waiters", schema="public")
    op.drop_table("back_in_stock_settings", schema="public")
