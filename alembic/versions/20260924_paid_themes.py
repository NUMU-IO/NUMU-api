"""Paid themes: one-time purchases from the merchant wallet.

Revision ID: paid_themes_20260924
Revises: merge_billing_themes_0924
Create Date: 2026-09-24

Additive and idempotent (IF NOT EXISTS):
- ``marketplace_themes.pending_price_cents``: a partner's new price, applied
  when an admin approves the theme's next version;
- ``marketplace_theme_purchases``: ``store_id``, ``tenant_id`` and
  ``wallet_transaction_id`` for wallet purchases (a purchase belongs to one
  store), and one succeeded purchase per store and theme;
- ``partner_ledger_entries.theme_id`` and ``app_fee_invoices.theme_id``: a
  theme sale is booked and invoiced like an app charge.

Downgrade drops the columns and the index.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "paid_themes_20260924"
down_revision: str | Sequence[str] | None = "merge_billing_themes_0924"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE public.marketplace_themes "
        "ADD COLUMN IF NOT EXISTS pending_price_cents INTEGER"
    )
    op.execute(
        "ALTER TABLE public.marketplace_theme_purchases "
        "ADD COLUMN IF NOT EXISTS store_id UUID, "
        "ADD COLUMN IF NOT EXISTS tenant_id UUID "
        "REFERENCES public.tenants(id) ON DELETE CASCADE, "
        "ADD COLUMN IF NOT EXISTS wallet_transaction_id UUID "
        "REFERENCES public.wallet_transactions(id) ON DELETE SET NULL"
    )
    op.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_theme_purchase_store_succeeded "
        "ON public.marketplace_theme_purchases (store_id, marketplace_theme_id) "
        "WHERE store_id IS NOT NULL AND status = 'succeeded'"
    )
    op.execute(
        "ALTER TABLE public.partner_ledger_entries "
        "ADD COLUMN IF NOT EXISTS theme_id UUID "
        "REFERENCES public.marketplace_themes(id) ON DELETE SET NULL"
    )
    op.execute(
        "ALTER TABLE public.app_fee_invoices "
        "ADD COLUMN IF NOT EXISTS theme_id UUID "
        "REFERENCES public.marketplace_themes(id) ON DELETE SET NULL"
    )


def downgrade() -> None:
    op.execute("ALTER TABLE public.app_fee_invoices DROP COLUMN IF EXISTS theme_id")
    op.execute(
        "ALTER TABLE public.partner_ledger_entries DROP COLUMN IF EXISTS theme_id"
    )
    op.execute("DROP INDEX IF EXISTS public.uq_theme_purchase_store_succeeded")
    op.execute(
        "ALTER TABLE public.marketplace_theme_purchases "
        "DROP COLUMN IF EXISTS wallet_transaction_id, "
        "DROP COLUMN IF EXISTS tenant_id, "
        "DROP COLUMN IF EXISTS store_id"
    )
    op.execute(
        "ALTER TABLE public.marketplace_themes DROP COLUMN IF EXISTS pending_price_cents"
    )
