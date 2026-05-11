"""Add shopify_subscriptions table for billing-sync (backend-001).

Revision ID: shopify_subs_20260508
Revises: wishlist_items_20260508
Create Date: 2026-05-08

Persists the merchant's Shopify Billing API subscription record so the
Numu dashboard + admin tools can display plan + cycle without
re-querying Shopify on every page load. Source of truth remains
Shopify; this row is upserted by the Shopify-app's
syncSubscriptionToNumu helper at
numu-payments-intelligence/app/lib/billing/billing.server.ts.

Schema decisions:
  * tenant_id NULLABLE without FK — matches the existing
    shopify_app_settings / shopify_installation pattern. RLS on Shopify
    tables is applied at the application layer (verify_internal_key
    dependency on every route), not at the row-level. Aligning with the
    pattern set in 20260301_add_shopify_tables and confirmed in
    20260327_trust_network_tables.
  * store_id NULLABLE=False without FK — same reason. The Shopify-app
    side resolves the store via /shopify/auth/lookup so we don't need
    referential integrity here.
  * UNIQUE (store_id, shopify_subscription_id) — clicking subscribe
    twice on the same Shopify subscription is a no-op upsert. Storing
    historic rows (cancelled then re-subscribed under a new sub_id) is
    allowed because the unique key includes the Shopify sub_id.
  * Index on status — supports "find active subscription for store"
    queries without a full scan.
  * cancelled_at NULLABLE — set the first time status transitions to
    a terminal state (CANCELLED / EXPIRED / FROZEN); preserved across
    retries via COALESCE in the upsert use case.

No backfill needed — new table for a feature that Shopify-app v002
just shipped.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

from alembic import op

revision: str = "shopify_subs_20260508"
down_revision: str | None = "wishlist_items_20260508"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "shopify_subscriptions",
        sa.Column(
            "id",
            UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "tenant_id",
            UUID(as_uuid=True),
            nullable=True,
        ),
        sa.Column(
            "store_id",
            UUID(as_uuid=True),
            nullable=False,
        ),
        sa.Column(
            "shopify_subscription_id",
            sa.String(length=255),
            nullable=False,
        ),
        sa.Column(
            "status",
            sa.String(length=32),
            nullable=False,
        ),
        sa.Column(
            "plan_id",
            sa.String(length=32),
            nullable=False,
        ),
        sa.Column(
            "is_trial",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
        sa.Column(
            "trial_ends_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
        sa.Column(
            "current_period_end",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
        sa.Column(
            "cancelled_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
        sa.Column(
            "synced_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
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
            "store_id",
            "shopify_subscription_id",
            name="uq_shopify_subscription_store_sub",
        ),
        schema="public",
    )

    op.create_index(
        "ix_shopify_subscriptions_tenant",
        "shopify_subscriptions",
        ["tenant_id"],
        unique=False,
        schema="public",
    )
    op.create_index(
        "ix_shopify_subscriptions_store",
        "shopify_subscriptions",
        ["store_id"],
        unique=False,
        schema="public",
    )
    op.create_index(
        "ix_shopify_subscriptions_status",
        "shopify_subscriptions",
        ["status"],
        unique=False,
        schema="public",
    )


def downgrade() -> None:
    op.drop_index(
        "ix_shopify_subscriptions_status",
        table_name="shopify_subscriptions",
        schema="public",
    )
    op.drop_index(
        "ix_shopify_subscriptions_store",
        table_name="shopify_subscriptions",
        schema="public",
    )
    op.drop_index(
        "ix_shopify_subscriptions_tenant",
        table_name="shopify_subscriptions",
        schema="public",
    )
    op.drop_table("shopify_subscriptions", schema="public")
