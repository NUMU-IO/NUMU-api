"""Partner referrals and the public partner directory.

Revision ID: referrals_dir_20260924
Revises: partner_members_20260924
Create Date: 2026-09-24

Additive and idempotent (IF NOT EXISTS):
- ``partner_accounts``: referral code and terms (2000 bps for 12 months by
  default), the opt-in directory profile, the admin ``verified`` badge and
  ``directory_hidden`` override;
- ``partner_referrals``: the partner that brought each tenant, one per
  tenant;
- ``partner_ledger_entries.tenant_id``: the referred merchant behind a
  ``referral`` credit.

Downgrade drops all of it.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "referrals_dir_20260924"
down_revision: str | Sequence[str] | None = "partner_members_20260924"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        ALTER TABLE public.partner_accounts
            ADD COLUMN IF NOT EXISTS referral_code VARCHAR(16),
            ADD COLUMN IF NOT EXISTS referral_bps INTEGER NOT NULL DEFAULT 2000,
            ADD COLUMN IF NOT EXISTS referral_months INTEGER NOT NULL DEFAULT 12,
            ADD COLUMN IF NOT EXISTS directory_listed BOOLEAN NOT NULL DEFAULT false,
            ADD COLUMN IF NOT EXISTS directory_profile JSONB,
            ADD COLUMN IF NOT EXISTS verified BOOLEAN NOT NULL DEFAULT false,
            ADD COLUMN IF NOT EXISTS directory_hidden BOOLEAN NOT NULL DEFAULT false
        """
    )
    op.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_partner_accounts_referral_code "
        "ON public.partner_accounts (referral_code)"
    )
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS public.partner_referrals (
            id UUID PRIMARY KEY,
            partner_id UUID NOT NULL
                REFERENCES public.partner_accounts(id) ON DELETE CASCADE,
            tenant_id UUID NOT NULL UNIQUE
                REFERENCES public.tenants(id) ON DELETE CASCADE,
            first_paid_at TIMESTAMPTZ,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_partner_referrals_partner "
        "ON public.partner_referrals (partner_id)"
    )
    op.execute(
        "ALTER TABLE public.partner_ledger_entries ADD COLUMN IF NOT EXISTS "
        "tenant_id UUID REFERENCES public.tenants(id) ON DELETE SET NULL"
    )


def downgrade() -> None:
    op.execute(
        "ALTER TABLE public.partner_ledger_entries DROP COLUMN IF EXISTS tenant_id"
    )
    op.execute("DROP TABLE IF EXISTS public.partner_referrals")
    op.execute("DROP INDEX IF EXISTS public.uq_partner_accounts_referral_code")
    op.execute(
        """
        ALTER TABLE public.partner_accounts
            DROP COLUMN IF EXISTS directory_hidden,
            DROP COLUMN IF EXISTS verified,
            DROP COLUMN IF EXISTS directory_profile,
            DROP COLUMN IF EXISTS directory_listed,
            DROP COLUMN IF EXISTS referral_months,
            DROP COLUMN IF EXISTS referral_bps,
            DROP COLUMN IF EXISTS referral_code
        """
    )
