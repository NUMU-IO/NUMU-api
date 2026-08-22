"""Merchant notification feed table.

Backs the hub's bell dropdown + Notifications page (Zid-style: tabs for
orders / abandoned carts / payments / logistics, mark-all-read, unread
badge). Rows are written by EventBus handlers + the abandoned-cart task.

Idempotent: CREATE TABLE / INDEX IF NOT EXISTS, safe to re-run.

Revision ID: merchant_notifs_20260822
Revises: merge_thread_ttclid_20260822
Create Date: 2026-08-22
"""

from alembic import op

# revision identifiers, used by Alembic.
revision = "merchant_notifs_20260822"
down_revision = "merge_thread_ttclid_20260822"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS public.merchant_notifications (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id UUID NOT NULL
                REFERENCES public.tenants(id) ON DELETE CASCADE,
            store_id UUID NOT NULL
                REFERENCES public.stores(id) ON DELETE CASCADE,
            category VARCHAR(30) NOT NULL,
            kind VARCHAR(50) NOT NULL,
            data JSONB,
            link VARCHAR(500),
            entity_type VARCHAR(30),
            entity_id UUID,
            is_important BOOLEAN NOT NULL DEFAULT FALSE,
            dedupe_key VARCHAR(200),
            read_at TIMESTAMPTZ,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_merchant_notifications_tenant_id "
        "ON public.merchant_notifications (tenant_id)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_merchant_notifications_store_id "
        "ON public.merchant_notifications (store_id)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_merchant_notifications_store_created "
        "ON public.merchant_notifications (store_id, created_at DESC)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS "
        "ix_merchant_notifications_store_category_created "
        "ON public.merchant_notifications (store_id, category, created_at DESC)"
    )
    # Partial index: the unread-count query only ever touches unread rows.
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_merchant_notifications_store_unread "
        "ON public.merchant_notifications (store_id) WHERE read_at IS NULL"
    )
    op.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_merchant_notifications_store_dedupe "
        "ON public.merchant_notifications (store_id, dedupe_key)"
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS public.merchant_notifications")
