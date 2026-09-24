"""Paid apps: per-partner share, VAT on NUMU's fee, partner coupons.

Revision ID: billing_followup_20260924
Revises: merge_appbill_members_0924
Create Date: 2026-09-24

Additive and idempotent (IF NOT EXISTS), public schema without RLS like the
other app billing tables:
- ``partner_accounts.share_bps``: the partner's share, null = default 80%;
- ``partner_ledger_entries``: the share, coupon discount and NUMU's VAT a
  sale was booked with;
- ``app_coupons`` and ``app_coupon_redemptions`` (one per store and coupon);
- ``app_subscriptions.coupon_id`` / ``coupon_cycles_left``;
- ``app_subscriptions.vat_grandfathered``: true for every subscription that
  exists when this runs (they keep renewing without VAT until they end or
  are subscribed again), false for new ones. Added once: a rerun never
  grandfathers later rows;
- ``app_fee_invoices``: NUMU's numbered invoice (and credit note) for its
  fee and the VAT on it, one per wallet charge.

Downgrade drops the tables and columns.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "billing_followup_20260924"
down_revision: str | Sequence[str] | None = "merge_appbill_members_0924"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE public.partner_accounts "
        "ADD COLUMN IF NOT EXISTS share_bps INTEGER "
        "CHECK (share_bps BETWEEN 0 AND 10000)"
    )
    op.execute(
        "ALTER TABLE public.partner_ledger_entries "
        "ADD COLUMN IF NOT EXISTS share_bps INTEGER, "
        "ADD COLUMN IF NOT EXISTS discount_cents BIGINT, "
        "ADD COLUMN IF NOT EXISTS vat_cents BIGINT"
    )
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS public.app_coupons (
            id UUID PRIMARY KEY,
            partner_id UUID NOT NULL
                REFERENCES public.partner_accounts(id) ON DELETE CASCADE,
            app_id UUID NOT NULL REFERENCES public.apps(id) ON DELETE CASCADE,
            code VARCHAR(40) NOT NULL,
            percent_off INTEGER CHECK (percent_off BETWEEN 1 AND 100),
            amount_off_cents INTEGER CHECK (amount_off_cents > 0),
            duration_cycles INTEGER CHECK (duration_cycles > 0),
            max_redemptions INTEGER CHECK (max_redemptions > 0),
            expires_at TIMESTAMPTZ,
            store_id UUID,
            active BOOLEAN NOT NULL DEFAULT true,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            CONSTRAINT uq_app_coupons_app_code UNIQUE (app_id, code),
            CONSTRAINT ck_app_coupons_one_discount
                CHECK ((percent_off IS NULL) <> (amount_off_cents IS NULL))
        )
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_public_app_coupons_partner_id "
        "ON public.app_coupons (partner_id)"
    )
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS public.app_coupon_redemptions (
            id UUID PRIMARY KEY,
            coupon_id UUID NOT NULL
                REFERENCES public.app_coupons(id) ON DELETE CASCADE,
            store_id UUID NOT NULL,
            tenant_id UUID NOT NULL
                REFERENCES public.tenants(id) ON DELETE CASCADE,
            subscription_id UUID
                REFERENCES public.app_subscriptions(id) ON DELETE SET NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            CONSTRAINT uq_app_coupon_redemption UNIQUE (coupon_id, store_id)
        )
        """
    )
    op.execute(
        "ALTER TABLE public.app_subscriptions "
        "ADD COLUMN IF NOT EXISTS coupon_id UUID "
        "REFERENCES public.app_coupons(id) ON DELETE SET NULL, "
        "ADD COLUMN IF NOT EXISTS coupon_cycles_left INTEGER"
    )
    op.execute(
        """
        DO $$
        BEGIN
            IF NOT EXISTS (
                SELECT 1 FROM information_schema.columns
                WHERE table_schema = 'public'
                  AND table_name = 'app_subscriptions'
                  AND column_name = 'vat_grandfathered'
            ) THEN
                ALTER TABLE public.app_subscriptions
                    ADD COLUMN vat_grandfathered BOOLEAN NOT NULL DEFAULT true;
                ALTER TABLE public.app_subscriptions
                    ALTER COLUMN vat_grandfathered SET DEFAULT false;
            END IF;
        END $$;
        """
    )
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS public.app_fee_invoices (
            id UUID PRIMARY KEY,
            number VARCHAR(32) NOT NULL,
            kind VARCHAR(20) NOT NULL,
            tenant_id UUID NOT NULL REFERENCES public.tenants(id) ON DELETE CASCADE,
            store_id UUID,
            app_id UUID REFERENCES public.apps(id) ON DELETE SET NULL,
            wallet_transaction_id UUID NOT NULL
                REFERENCES public.wallet_transactions(id) ON DELETE CASCADE,
            original_id UUID
                REFERENCES public.app_fee_invoices(id) ON DELETE SET NULL,
            list_price_cents BIGINT NOT NULL,
            discount_cents BIGINT NOT NULL DEFAULT 0,
            fee_cents BIGINT NOT NULL,
            vat_cents BIGINT NOT NULL,
            vat_bps INTEGER NOT NULL,
            share_bps INTEGER NOT NULL,
            total_cents BIGINT NOT NULL,
            currency VARCHAR(3) NOT NULL DEFAULT 'EGP',
            description VARCHAR(255) NOT NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            CONSTRAINT uq_app_fee_invoices_number UNIQUE (number),
            CONSTRAINT uq_app_fee_invoices_tx_kind
                UNIQUE (wallet_transaction_id, kind)
        )
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_app_fee_invoices_tenant_created "
        "ON public.app_fee_invoices (tenant_id, created_at)"
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS public.app_fee_invoices")
    op.execute(
        "ALTER TABLE public.app_subscriptions "
        "DROP COLUMN IF EXISTS vat_grandfathered, "
        "DROP COLUMN IF EXISTS coupon_cycles_left, "
        "DROP COLUMN IF EXISTS coupon_id"
    )
    op.execute("DROP TABLE IF EXISTS public.app_coupon_redemptions")
    op.execute("DROP TABLE IF EXISTS public.app_coupons")
    op.execute(
        "ALTER TABLE public.partner_ledger_entries "
        "DROP COLUMN IF EXISTS vat_cents, "
        "DROP COLUMN IF EXISTS discount_cents, "
        "DROP COLUMN IF EXISTS share_bps"
    )
    op.execute("ALTER TABLE public.partner_accounts DROP COLUMN IF EXISTS share_bps")
