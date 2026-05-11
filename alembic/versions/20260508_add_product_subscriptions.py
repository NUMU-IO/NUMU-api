"""Add product_subscriptions table for back-in-stock notifications.

Revision ID: product_subscriptions_20260508
Revises: marketplace_rls_20260507
Create Date: 2026-05-08

Phase 3.5 of the Shopify-parity audit. Customers ask to be notified
when an out-of-stock product comes back. The Celery sweep task scans
this table hourly for products that flipped to in-stock since the last
sweep and emails subscribers, stamping `notified_at` so the same row
isn't re-sent on the next sweep.

Schema decisions:
  * UNIQUE (product_id, variant_id, email): clicking "Notify me" twice
    on the same SKU is a no-op upsert. Variant-scoped subscriptions
    (Large/Blue specifically) coexist with product-level ones (any
    variant in stock).
  * Partial index on `WHERE notified_at IS NULL`: keeps the hot path
    cheap once a row is delivered, the index drops it.
  * tenant_id column — required by the platform's RLS pattern (every
    tenant-scoped table carries it). Customers don't need to touch it;
    the route layer fills from the resolved store.
  * created_at + updated_at via the standard timestamp mixin.

No backfill needed — this is a new feature; existing stores start with
zero subscriptions.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

from alembic import op

revision: str = "product_subscriptions_20260508"
down_revision: str | None = "marketplace_rls_20260507"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "product_subscriptions",
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
            "product_id",
            UUID(as_uuid=True),
            sa.ForeignKey("public.products.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "variant_id",
            UUID(as_uuid=True),
            nullable=True,
        ),
        sa.Column(
            "email",
            sa.String(length=254),
            nullable=False,
        ),
        sa.Column(
            "notified_at",
            sa.DateTime(timezone=True),
            nullable=True,
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
            "product_id",
            "variant_id",
            "email",
            name="uq_product_subscription_target",
        ),
        schema="public",
    )

    # Hot-path index for the sweep: only scan rows still pending.
    op.create_index(
        "ix_product_subscriptions_pending",
        "product_subscriptions",
        ["store_id", "product_id"],
        unique=False,
        schema="public",
        postgresql_where=sa.text("notified_at IS NULL"),
    )
    # Foreign-key support indexes (PG doesn't add these automatically).
    op.create_index(
        "ix_product_subscriptions_tenant",
        "product_subscriptions",
        ["tenant_id"],
        unique=False,
        schema="public",
    )
    op.create_index(
        "ix_product_subscriptions_store",
        "product_subscriptions",
        ["store_id"],
        unique=False,
        schema="public",
    )
    op.create_index(
        "ix_product_subscriptions_product",
        "product_subscriptions",
        ["product_id"],
        unique=False,
        schema="public",
    )


def downgrade() -> None:
    op.drop_index(
        "ix_product_subscriptions_product",
        table_name="product_subscriptions",
        schema="public",
    )
    op.drop_index(
        "ix_product_subscriptions_store",
        table_name="product_subscriptions",
        schema="public",
    )
    op.drop_index(
        "ix_product_subscriptions_tenant",
        table_name="product_subscriptions",
        schema="public",
    )
    op.drop_index(
        "ix_product_subscriptions_pending",
        table_name="product_subscriptions",
        schema="public",
    )
    op.drop_table("product_subscriptions", schema="public")
