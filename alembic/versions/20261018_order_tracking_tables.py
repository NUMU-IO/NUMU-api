"""Order Tracking app: its parcels, retry requests, messages and settings.

The app's own tenant tables (docs/Plans/APPS/02-order-tracking, data model).
Nothing reads or writes them until a store installs the app. RLS on, the same
shape as every other tenant-scoped table.

Revision ID: order_tracking_20261018
Revises: users_admin_scope_20261014
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "order_tracking_20261018"
down_revision: str | Sequence[str] | None = "users_admin_scope_20261014"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

UUID = postgresql.UUID(as_uuid=True)
TABLES = (
    "order_tracking_parcels",
    "order_tracking_retry_requests",
    "order_tracking_messages",
    "order_tracking_settings",
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


def _order(**kw) -> sa.Column:
    return sa.Column(
        "order_id", UUID, sa.ForeignKey("public.orders.id", ondelete="CASCADE"), **kw
    )


def _id() -> sa.Column:
    return sa.Column(
        "id", UUID, primary_key=True, server_default=sa.text("gen_random_uuid()")
    )


def upgrade() -> None:
    op.create_table(
        "order_tracking_parcels",
        _store(primary_key=True),
        _order(primary_key=True),
        _tenant(),
        sa.Column("shipment_id", UUID, nullable=True),
        sa.Column("governorate_code", sa.String(8), nullable=True),
        sa.Column("city", sa.String(100), nullable=True),
        sa.Column("eta_min", sa.Date(), nullable=True),
        sa.Column("eta_max", sa.Date(), nullable=True),
        *_stamps(),
        schema="public",
    )
    op.create_index(
        "ix_order_tracking_parcels_created",
        "order_tracking_parcels",
        ["created_at"],
        schema="public",
    )
    op.create_table(
        "order_tracking_retry_requests",
        _id(),
        _store(nullable=False),
        _order(nullable=False),
        _tenant(),
        sa.Column("day", sa.Date(), nullable=False),
        sa.Column("window", sa.String(10), nullable=False),
        sa.Column("note", sa.String(200), nullable=True),
        sa.Column("handled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("courier_notified_at", sa.DateTime(timezone=True), nullable=True),
        *_stamps(),
        schema="public",
    )
    op.create_index(
        "ix_order_tracking_retry_store_order",
        "order_tracking_retry_requests",
        ["store_id", "order_id"],
        schema="public",
    )
    op.create_table(
        "order_tracking_messages",
        _id(),
        _store(nullable=False),
        _order(nullable=False),
        _tenant(),
        sa.Column("kind", sa.String(20), nullable=False),
        sa.Column("attempt", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(10), nullable=False),
        sa.Column("skip_reason", sa.String(64), nullable=True),
        sa.Column("provider_message_id", sa.String(255), nullable=True),
        sa.Column("send_at", sa.DateTime(timezone=True), nullable=True),
        *_stamps(),
        sa.UniqueConstraint(
            "store_id", "order_id", "kind", "attempt", name="uq_order_tracking_message"
        ),
        schema="public",
    )
    op.create_index(
        "ix_order_tracking_messages_store_created",
        "order_tracking_messages",
        ["store_id", "created_at"],
        schema="public",
    )
    op.create_table(
        "order_tracking_settings",
        _store(primary_key=True),
        _tenant(),
        sa.Column("out_for_delivery_enabled", sa.Boolean(), nullable=False),
        sa.Column("failed_enabled", sa.Boolean(), nullable=False),
        sa.Column("daily_cap", sa.Integer(), nullable=False),
        sa.Column("show_items", sa.Boolean(), nullable=False),
        sa.Column("show_contact", sa.Boolean(), nullable=False),
        *_stamps(),
        schema="public",
    )
    # Written out per table so scripts/check_migration_safety.py can read it.
    op.execute("ALTER TABLE public.order_tracking_parcels ENABLE ROW LEVEL SECURITY")
    op.execute(
        """
        CREATE POLICY tenant_isolation_order_tracking_parcels
        ON public.order_tracking_parcels
        USING (tenant_id = NULLIF(current_setting('app.current_tenant', true), '')::uuid)
        """
    )
    op.execute(
        "ALTER TABLE public.order_tracking_retry_requests ENABLE ROW LEVEL SECURITY"
    )
    op.execute(
        """
        CREATE POLICY tenant_isolation_order_tracking_retry_requests
        ON public.order_tracking_retry_requests
        USING (tenant_id = NULLIF(current_setting('app.current_tenant', true), '')::uuid)
        """
    )
    op.execute("ALTER TABLE public.order_tracking_messages ENABLE ROW LEVEL SECURITY")
    op.execute(
        """
        CREATE POLICY tenant_isolation_order_tracking_messages
        ON public.order_tracking_messages
        USING (tenant_id = NULLIF(current_setting('app.current_tenant', true), '')::uuid)
        """
    )
    op.execute("ALTER TABLE public.order_tracking_settings ENABLE ROW LEVEL SECURITY")
    op.execute(
        """
        CREATE POLICY tenant_isolation_order_tracking_settings
        ON public.order_tracking_settings
        USING (tenant_id = NULLIF(current_setting('app.current_tenant', true), '')::uuid)
        """
    )


def downgrade() -> None:
    for table in reversed(TABLES):
        op.execute(f"DROP POLICY IF EXISTS tenant_isolation_{table} ON public.{table}")
        op.drop_table(table, schema="public")
