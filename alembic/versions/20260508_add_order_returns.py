"""Add order_returns table for customer-initiated return requests.

Revision ID: order_returns_20260508
Revises: product_subscriptions_20260508
Create Date: 2026-05-08

Phase 3.1 of the Shopify-parity audit. Customer requests a return →
merchant approves → package received → refund issued. Sits next to the
existing refunds table; the FK on refunds.metadata.return_id (JSONB
field, no formal FK) links the two when an approved return mints a
matching refund.

Schema decisions:
  * Per-line items live in JSONB (line_items) rather than a separate
    table. We never query individual lines — the merchant edits the
    whole return as a unit. Avoids the join cost that a per-line table
    would impose on every return-list query for no benefit.
  * status / reason use Postgres enums named `returnstatus` /
    `returnreason`. Lowercase values to match the StrEnum convention
    other entities (couponstatus, invoicestatus) settled on after
    earlier mixed-case footguns.
  * tenant_id required (RLS pattern) + composite index on
    (store_id, status) to make the merchant hub's "pending returns"
    page cheap.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import ENUM, JSONB, UUID

from alembic import op

revision: str = "order_returns_20260508"
down_revision: str | None = "product_subscriptions_20260508"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Postgres enums for status + reason. Lowercase values for parity
    # with other StrEnum-backed enums in the schema.
    return_status = ENUM(
        "requested",
        "approved",
        "rejected",
        "received",
        "completed",
        "canceled",
        name="returnstatus",
        schema="public",
        create_type=False,
    )
    return_reason = ENUM(
        "defective",
        "wrong_item",
        "not_as_described",
        "size_or_fit",
        "no_longer_needed",
        "other",
        name="returnreason",
        schema="public",
        create_type=False,
    )
    return_status.create(op.get_bind(), checkfirst=True)
    return_reason.create(op.get_bind(), checkfirst=True)

    op.create_table(
        "order_returns",
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
            "order_id",
            UUID(as_uuid=True),
            sa.ForeignKey("public.orders.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "store_id",
            UUID(as_uuid=True),
            sa.ForeignKey("public.stores.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "customer_id",
            UUID(as_uuid=True),
            nullable=True,
        ),
        sa.Column("return_number", sa.String(length=50), nullable=False),
        sa.Column(
            "status",
            return_status,
            nullable=False,
            server_default="requested",
        ),
        sa.Column(
            "reason",
            return_reason,
            nullable=False,
            server_default="other",
        ),
        sa.Column("customer_note", sa.Text(), nullable=True),
        sa.Column("merchant_note", sa.Text(), nullable=True),
        sa.Column(
            "line_items",
            JSONB,
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column(
            "refund_id",
            UUID(as_uuid=True),
            sa.ForeignKey("public.refunds.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "requested_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("rejected_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("canceled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("approved_by", UUID(as_uuid=True), nullable=True),
        sa.Column("rejected_by", UUID(as_uuid=True), nullable=True),
        sa.Column("received_by", UUID(as_uuid=True), nullable=True),
        sa.Column("requested_amount", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "currency", sa.String(length=3), nullable=False, server_default="EGP"
        ),
        sa.Column(
            "extra_metadata",
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

    # Hot-path indexes for the hub's "pending returns" view + the
    # storefront's "my returns" view.
    op.create_index(
        "ix_order_returns_store_status",
        "order_returns",
        ["store_id", "status"],
        unique=False,
        schema="public",
    )
    op.create_index(
        "ix_order_returns_customer",
        "order_returns",
        ["customer_id"],
        unique=False,
        schema="public",
    )
    op.create_index(
        "ix_order_returns_order",
        "order_returns",
        ["order_id"],
        unique=False,
        schema="public",
    )
    op.create_index(
        "ix_order_returns_tenant",
        "order_returns",
        ["tenant_id"],
        unique=False,
        schema="public",
    )
    op.create_index(
        "ix_order_returns_return_number",
        "order_returns",
        ["return_number"],
        unique=False,
        schema="public",
    )


def downgrade() -> None:
    op.drop_index(
        "ix_order_returns_return_number",
        table_name="order_returns",
        schema="public",
    )
    op.drop_index(
        "ix_order_returns_tenant",
        table_name="order_returns",
        schema="public",
    )
    op.drop_index(
        "ix_order_returns_order",
        table_name="order_returns",
        schema="public",
    )
    op.drop_index(
        "ix_order_returns_customer",
        table_name="order_returns",
        schema="public",
    )
    op.drop_index(
        "ix_order_returns_store_status",
        table_name="order_returns",
        schema="public",
    )
    op.drop_table("order_returns", schema="public")
    sa.Enum(name="returnreason", schema="public").drop(op.get_bind(), checkfirst=True)
    sa.Enum(name="returnstatus", schema="public").drop(op.get_bind(), checkfirst=True)
