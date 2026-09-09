"""Keep the referral code a lead actually arrived with.

`_attribute_referral` resolved a code against `merchant_leads.referral_code`
and discarded it when nothing matched. That was fine while leads were the only
referral programme, but a merchant's own code (`STORENAME-NUMU-XXXX`) belongs
to `merchant_referrals` and never matches a lead — so every merchant referral
was thrown away at signup, and `merchant_referrals` sat at zero rows in
production.

The code cannot be redeemed at signup: a referral is tenant-to-tenant and the
referred merchant has no tenant until they create their store. So it is parked
here and applied at store creation.

Revision ID: lead_referral_used_20260909
Revises: marketing_click_20260909
"""

from alembic import op

revision = "lead_referral_used_20260909"
down_revision = "marketing_click_20260909"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE public.merchant_leads "
        "ADD COLUMN IF NOT EXISTS referral_code_used VARCHAR(64)"
    )
    # Partial: only rows with an unredeemed code are ever scanned, and that is
    # a small slice of a table that holds every lead ever captured.
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_merchant_leads_referral_code_used "
        "ON public.merchant_leads (referral_code_used) "
        "WHERE referral_code_used IS NOT NULL"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS public.ix_merchant_leads_referral_code_used")
    op.execute(
        "ALTER TABLE public.merchant_leads DROP COLUMN IF EXISTS referral_code_used"
    )
