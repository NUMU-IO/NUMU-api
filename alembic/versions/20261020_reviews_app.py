"""Reviews app: its columns on product_reviews, its requests and settings.

The app works on core ``product_reviews`` directly (docs/Plans/APPS/03-reviews,
§ Version 1.0): nullable columns only, so every existing row and every reader
stays as it is. Its own two tables follow the shape of the other app tables:
tenant-scoped, RLS on. Nothing writes any of it until a store installs the app.

Revision ID: reviews_app_20261020
Revises: rev_templates_20261020
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "reviews_app_20261020"
down_revision: str | Sequence[str] | None = "rev_templates_20261020"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

UUID = postgresql.UUID(as_uuid=True)
TABLES = ("reviews_requests", "reviews_settings")
COLUMNS = (
    "media",
    "replied_at",
    "reply_body",
    "rejected_reason",
    "verified",
    "order_id",
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


def upgrade() -> None:
    # Nullable columns, and a constant default: no table rewrite on Postgres 11+.
    op.add_column(
        "product_reviews",
        sa.Column(
            "order_id",
            UUID,
            sa.ForeignKey("public.orders.id", ondelete="SET NULL"),
            nullable=True,
        ),
        schema="public",
    )
    op.add_column(
        "product_reviews",
        sa.Column(
            "verified", sa.Boolean(), nullable=False, server_default=sa.text("false")
        ),
        schema="public",
    )
    op.add_column(
        "product_reviews",
        sa.Column("rejected_reason", sa.String(16), nullable=True),
        schema="public",
    )
    op.add_column(
        "product_reviews",
        sa.Column("reply_body", sa.Text(), nullable=True),
        schema="public",
    )
    op.add_column(
        "product_reviews",
        sa.Column("replied_at", sa.DateTime(timezone=True), nullable=True),
        schema="public",
    )
    op.add_column(
        "product_reviews",
        sa.Column("media", postgresql.JSONB(), nullable=True),
        schema="public",
    )
    # Every existing row has order_id NULL, so the index starts empty.
    op.create_index(
        "uq_product_reviews_order_product",
        "product_reviews",
        ["store_id", "order_id", "product_id"],
        unique=True,
        schema="public",
        postgresql_where=sa.text("order_id IS NOT NULL"),
    )

    op.create_table(
        "reviews_requests",
        sa.Column(
            "id", UUID, primary_key=True, server_default=sa.text("gen_random_uuid()")
        ),
        _store(nullable=False),
        sa.Column(
            "order_id",
            UUID,
            sa.ForeignKey("public.orders.id", ondelete="CASCADE"),
            nullable=False,
        ),
        _tenant(),
        sa.Column("status", sa.String(10), nullable=False),
        sa.Column("skip_reason", sa.String(64), nullable=True),
        sa.Column("due_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("token_hash", sa.String(64), nullable=True, unique=True),
        sa.Column("provider_message_id", sa.String(255), nullable=True),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("opened_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        *_stamps(),
        sa.UniqueConstraint("store_id", "order_id", name="uq_reviews_request_order"),
        schema="public",
    )
    op.create_index(
        "ix_reviews_requests_status_due",
        "reviews_requests",
        ["status", "due_at"],
        schema="public",
    )
    op.create_index(
        "ix_reviews_requests_store_created",
        "reviews_requests",
        ["store_id", "created_at"],
        schema="public",
    )
    op.create_table(
        "reviews_settings",
        _store(primary_key=True),
        _tenant(),
        sa.Column("requests_enabled", sa.Boolean(), nullable=False),
        sa.Column("delay_days", sa.Integer(), nullable=False),
        sa.Column("daily_cap", sa.Integer(), nullable=False),
        sa.Column("photos_enabled", sa.Boolean(), nullable=False),
        *_stamps(),
        schema="public",
    )
    # Written out per table so scripts/check_migration_safety.py can read it.
    op.execute("ALTER TABLE public.reviews_requests ENABLE ROW LEVEL SECURITY")
    op.execute(
        """
        CREATE POLICY tenant_isolation_reviews_requests
        ON public.reviews_requests
        USING (tenant_id = NULLIF(current_setting('app.current_tenant', true), '')::uuid)
        """
    )
    op.execute("ALTER TABLE public.reviews_settings ENABLE ROW LEVEL SECURITY")
    op.execute(
        """
        CREATE POLICY tenant_isolation_reviews_settings
        ON public.reviews_settings
        USING (tenant_id = NULLIF(current_setting('app.current_tenant', true), '')::uuid)
        """
    )


def downgrade() -> None:
    for table in reversed(TABLES):
        op.execute(f"DROP POLICY IF EXISTS tenant_isolation_{table} ON public.{table}")
        op.drop_table(table, schema="public")
    op.drop_index(
        "uq_product_reviews_order_product",
        table_name="product_reviews",
        schema="public",
    )
    for column in COLUMNS:
        op.drop_column("product_reviews", column, schema="public")
