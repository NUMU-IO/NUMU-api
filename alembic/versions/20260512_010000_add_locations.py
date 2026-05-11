"""Add locations table — Phase 7.2 pickup-location slot.

Revision ID: locations_20260512
Revises: byot_static_templates_20260511
Create Date: 2026-05-12

Phase 7.2 of the BYOT-completeness sprint. A `Location` is a physical
or logical site the merchant operates from. Today the table powers
the storefront checkout's "Pick up in-store" option; the multi-
location inventory join (InventoryLevel) lands in Phase 8.2.

Schema:
* `tenant_id` + `store_id` for RLS (public schema, store-scoped).
* `address` is JSONB — the Address value object is stored inline to
  save a join on every location read.
* Partial index on `(store_id, position) WHERE is_active AND
  fulfills_pickup` for the storefront's pickup picker hot path.

No backfill — existing stores start with zero locations. The hub
merchant UI gains a "Locations" tab in store settings that lets the
merchant create their first one.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB, UUID

from alembic import op

revision: str = "locations_20260512"
down_revision: str | None = "byot_static_templates_20260511"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "locations",
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
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("name_ar", sa.String(255), nullable=True),
        sa.Column(
            "address",
            JSONB,
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "fulfills_orders", sa.Boolean, nullable=False, server_default=sa.true()
        ),
        sa.Column(
            "fulfills_pickup",
            sa.Boolean,
            nullable=False,
            server_default=sa.false(),
        ),
        sa.Column("pickup_instructions", sa.String(1024), nullable=True),
        sa.Column("pickup_instructions_ar", sa.String(1024), nullable=True),
        sa.Column("is_active", sa.Boolean, nullable=False, server_default=sa.true()),
        sa.Column("position", sa.Integer, nullable=False, server_default=sa.text("0")),
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
        "ix_locations_tenant",
        "locations",
        ["tenant_id"],
        unique=False,
        schema="public",
    )
    op.create_index(
        "ix_locations_store",
        "locations",
        ["store_id"],
        unique=False,
        schema="public",
    )
    op.create_index(
        "ix_locations_pickup",
        "locations",
        ["store_id", "position"],
        unique=False,
        schema="public",
        postgresql_where=sa.text("is_active = true AND fulfills_pickup = true"),
    )


def downgrade() -> None:
    op.drop_index("ix_locations_pickup", table_name="locations", schema="public")
    op.drop_index("ix_locations_store", table_name="locations", schema="public")
    op.drop_index("ix_locations_tenant", table_name="locations", schema="public")
    op.drop_table("locations", schema="public")
