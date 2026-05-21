"""Add performance indexes for sorting and cursor pagination.

Revision ID: 3f7a2e91c4b8
Revises: 9c6da865096f
Create Date: 2026-02-06

Indexes added:
- orders.created_at: ORDER BY optimization for order listings and date-range filters
- (customer_id, created_at DESC) on orders: Customer order history (filter + sort)
- products.created_at: Cursor pagination sort in storefront browse
- (store_id, created_at DESC) on products: Storefront cursor pagination covering index

Note: product.store_id and order.customer_id already have individual indexes
(defined in model __table_args__). The single-column created_at indexes and
composite indexes added here optimize the hot-path queries identified in:
- OrderRepository.get_by_customer() — filters customer_id, sorts created_at DESC
- OrderRepository.get_by_date_range() — filters store_id + created_at range
- ProductRepository.list_with_cursor() — filters store_id, sorts created_at DESC

For production deployment on large tables, consider running these as
CREATE INDEX CONCURRENTLY outside of a transaction to avoid table locks.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "3f7a2e91c4b8"
down_revision: str | None = "9c6da865096f"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # orders.created_at — single column for ORDER BY in listings and date-range filters
    op.create_index(
        "ix_orders_created_at",
        "orders",
        ["created_at"],
        unique=False,
        schema="public",
        if_not_exists=True,
    )

    # Composite: customer order history (customer_id + created_at DESC)
    # Covers OrderRepository.get_by_customer() which filters by customer_id
    # and sorts by created_at DESC
    op.create_index(
        "ix_orders_customer_created_at",
        "orders",
        ["customer_id", sa.text("created_at DESC")],
        unique=False,
        schema="public",
        if_not_exists=True,
    )

    # products.created_at — single column for cursor pagination ORDER BY
    op.create_index(
        "ix_products_created_at",
        "products",
        ["created_at"],
        unique=False,
        schema="public",
        if_not_exists=True,
    )

    # Composite: storefront cursor pagination (store_id + created_at DESC)
    # Covers ProductRepository.list_with_cursor() which filters by store_id
    # and sorts by created_at DESC, id DESC
    op.create_index(
        "ix_products_store_created_at",
        "products",
        ["store_id", sa.text("created_at DESC")],
        unique=False,
        schema="public",
        if_not_exists=True,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_products_store_created_at",
        table_name="products",
        schema="public",
    )
    op.drop_index(
        "ix_products_created_at",
        table_name="products",
        schema="public",
    )
    op.drop_index(
        "ix_orders_customer_created_at",
        table_name="orders",
        schema="public",
    )
    op.drop_index(
        "ix_orders_created_at",
        table_name="orders",
        schema="public",
    )
