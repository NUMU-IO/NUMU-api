"""Add composite indexes for frequent query patterns.

Revision ID: e5f6a7b8c9d0
Revises: d4e5f6a7b8c9
Create Date: 2026-02-04
"""

from alembic import op

revision = "e5f6a7b8c9d0"
down_revision = "d4e5f6a7b8c9"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Customers: filtered by store + email (login, uniqueness checks)
    op.create_index(
        "ix_customers_store_email",
        "customers",
        ["store_id", "email"],
        unique=False,
        schema="public",
        if_not_exists=True,
    )

    # Orders: filtered by store + status (dashboard order lists)
    op.create_index(
        "ix_orders_store_status",
        "orders",
        ["store_id", "status"],
        unique=False,
        schema="public",
        if_not_exists=True,
    )

    # Orders: filtered by store + payment_status (payment reconciliation)
    op.create_index(
        "ix_orders_store_payment_status",
        "orders",
        ["store_id", "payment_status"],
        unique=False,
        schema="public",
        if_not_exists=True,
    )

    # Products: filtered by store + status (storefront catalog, active filtering)
    op.create_index(
        "ix_products_store_status",
        "products",
        ["store_id", "status"],
        unique=False,
        schema="public",
        if_not_exists=True,
    )


def downgrade() -> None:
    op.drop_index("ix_products_store_status", table_name="products", schema="public")
    op.drop_index(
        "ix_orders_store_payment_status", table_name="orders", schema="public"
    )
    op.drop_index("ix_orders_store_status", table_name="orders", schema="public")
    op.drop_index("ix_customers_store_email", table_name="customers", schema="public")
