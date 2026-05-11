"""Add inventory_levels + inventory_transfers + backfill — Phase 8.2.

Revision ID: inventory_multiloc_20260615
Revises: product_variants_20260601
Create Date: 2026-06-15

Phase 8.2 of the Shopify-parity roadmap. Two new tables and a
backfill that wires per-location inventory tracking on top of the
single-location stock model that landed in Phase 8.1.

Schema:

1. `inventory_levels` — (variant × location) join carrying the
   per-location stock count. UNIQUE on (variant_id, location_id);
   `available` + `reserved` columns. Indexes for the three hot
   query patterns: by variant (PDP chip), by location (Hub Inventory
   page), by store (Hub Inventory dashboard rollup).

2. `inventory_transfers` — audit trail for moving stock between
   locations. State machine in the entity (DRAFT → REQUESTED →
   IN_TRANSIT → RECEIVED, plus CANCELED). Lines embedded as JSONB
   because typical transfers are <20 lines and we always read/write
   them whole.

Backfill:

For every store that has at least one Location row with
`fulfills_orders=true`, we pick the lowest-position location and
create one `inventory_levels` row per variant at that location,
seeded with the variant's current `inventory_quantity`. Stores with
zero locations get skipped — they're not using multi-location yet,
and the application layer's "no levels → fall back to
variant.inventory_quantity" branch keeps their checkout working.

The backfill is idempotent (`ON CONFLICT DO NOTHING` on
(variant_id, location_id)) so re-running the migration in test/stage
is safe.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB, UUID

from alembic import op

revision: str = "inventory_multiloc_20260615"
down_revision: str | None = "product_variants_20260601"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # ── 1. inventory_levels ───────────────────────────────────────
    op.create_table(
        "inventory_levels",
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
            "variant_id",
            UUID(as_uuid=True),
            sa.ForeignKey("public.product_variants.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "location_id",
            UUID(as_uuid=True),
            sa.ForeignKey("public.locations.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "available",
            sa.Integer,
            nullable=False,
            server_default=sa.text("0"),
        ),
        sa.Column(
            "reserved",
            sa.Integer,
            nullable=False,
            server_default=sa.text("0"),
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
        sa.UniqueConstraint(
            "variant_id", "location_id", name="uq_inventory_variant_location"
        ),
        schema="public",
    )
    op.create_index(
        "ix_inventory_levels_tenant",
        "inventory_levels",
        ["tenant_id"],
        unique=False,
        schema="public",
    )
    op.create_index(
        "ix_inventory_levels_variant",
        "inventory_levels",
        ["variant_id"],
        unique=False,
        schema="public",
    )
    op.create_index(
        "ix_inventory_levels_location",
        "inventory_levels",
        ["location_id"],
        unique=False,
        schema="public",
    )
    op.create_index(
        "ix_inventory_levels_store",
        "inventory_levels",
        ["store_id"],
        unique=False,
        schema="public",
    )

    # ── 2. inventory_transfers ────────────────────────────────────
    transfer_status = sa.Enum(
        "draft",
        "requested",
        "in_transit",
        "received",
        "canceled",
        name="transferstatus",
    )
    transfer_status.create(op.get_bind(), checkfirst=True)

    op.create_table(
        "inventory_transfers",
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
            "from_location_id",
            UUID(as_uuid=True),
            sa.ForeignKey("public.locations.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "to_location_id",
            UUID(as_uuid=True),
            sa.ForeignKey("public.locations.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "status",
            transfer_status,
            nullable=False,
            server_default="draft",
        ),
        sa.Column("note", sa.Text, nullable=True),
        sa.Column("carrier_reference", sa.String(128), nullable=True),
        sa.Column(
            "lines",
            JSONB,
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column("requested_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("shipped_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("canceled_at", sa.DateTime(timezone=True), nullable=True),
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
        "ix_transfers_store_status",
        "inventory_transfers",
        ["store_id", "status"],
        unique=False,
        schema="public",
    )
    op.create_index(
        "ix_transfers_from_location",
        "inventory_transfers",
        ["from_location_id"],
        unique=False,
        schema="public",
    )
    op.create_index(
        "ix_transfers_to_location",
        "inventory_transfers",
        ["to_location_id"],
        unique=False,
        schema="public",
    )

    # ── 3. Backfill: one InventoryLevel per variant @ store's first
    #       order-fulfilling location ────────────────────────────────
    #
    # We pick the lowest-position active+fulfills_orders location per
    # store via a window-function CTE. Stores with no such location
    # are skipped — they're not using multi-location yet and the
    # application layer falls back to variant.inventory_quantity.
    #
    # ON CONFLICT DO NOTHING keeps the backfill idempotent in case
    # the migration is re-run in test/staging.
    op.execute(
        sa.text("""
        WITH default_locations AS (
            SELECT DISTINCT ON (store_id)
                id AS location_id,
                tenant_id,
                store_id
            FROM public.locations
            WHERE is_active = true AND fulfills_orders = true
            ORDER BY store_id, position ASC, created_at ASC
        )
        INSERT INTO public.inventory_levels (
            id, tenant_id, store_id, variant_id, location_id,
            available, reserved, created_at, updated_at
        )
        SELECT
            gen_random_uuid(),
            dl.tenant_id,
            dl.store_id,
            v.id,
            dl.location_id,
            COALESCE(v.inventory_quantity, 0),
            0,
            NOW(),
            NOW()
        FROM public.product_variants v
        JOIN default_locations dl ON dl.store_id = v.store_id
        ON CONFLICT (variant_id, location_id) DO NOTHING
        """)
    )


def downgrade() -> None:
    op.drop_index(
        "ix_transfers_to_location",
        table_name="inventory_transfers",
        schema="public",
    )
    op.drop_index(
        "ix_transfers_from_location",
        table_name="inventory_transfers",
        schema="public",
    )
    op.drop_index(
        "ix_transfers_store_status",
        table_name="inventory_transfers",
        schema="public",
    )
    op.drop_table("inventory_transfers", schema="public")
    sa.Enum(name="transferstatus").drop(op.get_bind(), checkfirst=True)

    op.drop_index(
        "ix_inventory_levels_store",
        table_name="inventory_levels",
        schema="public",
    )
    op.drop_index(
        "ix_inventory_levels_location",
        table_name="inventory_levels",
        schema="public",
    )
    op.drop_index(
        "ix_inventory_levels_variant",
        table_name="inventory_levels",
        schema="public",
    )
    op.drop_index(
        "ix_inventory_levels_tenant",
        table_name="inventory_levels",
        schema="public",
    )
    op.drop_table("inventory_levels", schema="public")
