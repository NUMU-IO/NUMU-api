"""add location columns to customer_addresses

Adds GPS / reverse-geocoded location fields to the customer address book so
logged-in customers can reuse pinned Home/Work locations across orders, and
so future proximity queries (delivery zones, fraud clustering) can run.

All columns are nullable — the feature is optional and existing addresses
predate it.

Revision ID: cust_addr_loc_20260422
Revises: activate_tenants_broad_20260419
Create Date: 2026-04-22 10:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "cust_addr_loc_20260422"
down_revision: str | None = "activate_tenants_broad_20260419"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "customer_addresses",
        sa.Column("latitude", sa.Float(), nullable=True),
        schema="public",
    )
    op.add_column(
        "customer_addresses",
        sa.Column("longitude", sa.Float(), nullable=True),
        schema="public",
    )
    op.add_column(
        "customer_addresses",
        sa.Column("location_accuracy", sa.Float(), nullable=True),
        schema="public",
    )
    op.add_column(
        "customer_addresses",
        sa.Column("location_source", sa.String(length=20), nullable=True),
        schema="public",
    )
    op.add_column(
        "customer_addresses",
        sa.Column("geocoded_address", sa.String(length=500), nullable=True),
        schema="public",
    )


def downgrade() -> None:
    op.drop_column("customer_addresses", "geocoded_address", schema="public")
    op.drop_column("customer_addresses", "location_source", schema="public")
    op.drop_column("customer_addresses", "location_accuracy", schema="public")
    op.drop_column("customer_addresses", "longitude", schema="public")
    op.drop_column("customer_addresses", "latitude", schema="public")
