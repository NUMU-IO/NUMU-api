"""Complete the merchant intake record: WhatsApp, activation, business profile.

Four additions that finish what the intake redesign started.

**WhatsApp number.** ``users.phone`` is the number a merchant signed up
with; it is not necessarily the one they read messages on. One nullable
column rather than a boolean plus a number: NULL means "same as phone",
which is the answer for most merchants and cannot drift out of sync with
a separate flag the way two columns would. The ``whatsapp_same_as_phone``
tick on the signup form is this column being NULL.

**Activation milestones.** ``first_product_at`` and ``first_commission_at``
join ``first_order_at``. Together they are the merchant lifecycle we
could not previously answer questions about: did they build a store, did
they sell, did they start paying us.

**Business profile.** Registered-business status, tax id and where
non-COD money should land. Deliberately NOT on ``merchant_leads``: that
table has no foreign key and outlives the tenant on purpose, which is
exactly the wrong lifetime for a merchant's financial details. This one
cascades — when a tenant is deleted its bank details go with it.

Payout details are encrypted at rest with the same Fernet-based
SecretsManager used for channel credentials, and a masked tail is stored
alongside so the hub and admin can display "ending 4471" without
decrypting anything.

Nothing here gates anything. The fields are collected and surfaced; no
store is blocked from selling for want of a tax id.

Revision ID: intake_completion_20260902
Revises: lead_qualification_20260902
"""

import sqlalchemy as sa

from alembic import op

revision = "intake_completion_20260902"
down_revision = "lead_qualification_20260902"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ── WhatsApp number ───────────────────────────────────────────
    op.execute(
        sa.text(
            "ALTER TABLE public.users "
            "ADD COLUMN IF NOT EXISTS whatsapp_phone VARCHAR(20)"
        )
    )
    op.execute(
        sa.text(
            "ALTER TABLE public.merchant_leads "
            "ADD COLUMN IF NOT EXISTS whatsapp_phone VARCHAR(20)"
        )
    )

    # ── Remaining activation milestones ───────────────────────────
    for col in ("first_product_at", "first_commission_at"):
        op.execute(
            sa.text(
                f"ALTER TABLE public.merchant_leads "
                f"ADD COLUMN IF NOT EXISTS {col} TIMESTAMPTZ"
            )
        )

    # ── Business profile ──────────────────────────────────────────
    op.execute(
        sa.text(
            """
            CREATE TABLE IF NOT EXISTS public.merchant_business_profiles (
                id UUID PRIMARY KEY,
                created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                tenant_id UUID NOT NULL UNIQUE
                    REFERENCES public.tenants (id) ON DELETE CASCADE,
                -- NULL means unanswered, which is different from "no".
                is_registered_business BOOLEAN,
                tax_id VARCHAR(50),
                payout_bank_name VARCHAR(120),
                payout_account_name VARCHAR(160),
                -- Fernet ciphertext + the key that encrypted it, so a key
                -- rotation can find the rows it still needs to re-wrap.
                payout_encrypted BYTEA,
                payout_key_id VARCHAR(64),
                -- Last few characters, for display. Never the whole number.
                payout_masked VARCHAR(24),
                completed_at TIMESTAMPTZ
            )
            """
        )
    )
    op.execute(
        sa.text(
            "CREATE INDEX IF NOT EXISTS ix_merchant_business_profiles_tenant "
            "ON public.merchant_business_profiles (tenant_id)"
        )
    )

    # ── Backfill first_product_at ─────────────────────────────────
    # Earliest product per tenant. Merchants who built a store before this
    # deploy have a real first-product date; leaving it NULL would read as
    # "never added a product", which is the opposite of the truth.
    op.execute(
        sa.text(
            """
            UPDATE public.merchant_leads l
            SET first_product_at = f.first_created
            FROM (
                SELECT p.tenant_id, MIN(p.created_at) AS first_created
                FROM public.products p
                GROUP BY p.tenant_id
            ) f
            WHERE f.tenant_id = l.tenant_id
              AND l.first_product_at IS NULL
            """
        )
    )

    # ── Backfill first_commission_at ──────────────────────────────
    # The wallet ledger already holds every commission ever charged.
    op.execute(
        sa.text(
            """
            UPDATE public.merchant_leads l
            SET first_commission_at = f.first_charged
            FROM (
                SELECT w.tenant_id, MIN(w.created_at) AS first_charged
                FROM public.wallet_transactions w
                WHERE w.kind = 'commission'
                GROUP BY w.tenant_id
            ) f
            WHERE f.tenant_id = l.tenant_id
              AND l.first_commission_at IS NULL
            """
        )
    )

    # ── Seed business profiles from the tax id already on file ────
    # Merchants who filled in an invoice tax id have already told us they
    # are a registered business; making them type it twice would be a
    # worse question than not asking.
    op.execute(
        sa.text(
            """
            INSERT INTO public.merchant_business_profiles (
                id, created_at, updated_at, tenant_id,
                is_registered_business, tax_id
            )
            SELECT DISTINCT ON (s.tenant_id)
                gen_random_uuid(), now(), now(), s.tenant_id,
                TRUE,
                NULLIF(TRIM(s.settings -> 'invoice' ->> 'tax_id'), '')
            FROM public.stores s
            WHERE NULLIF(TRIM(s.settings -> 'invoice' ->> 'tax_id'), '') IS NOT NULL
            ORDER BY s.tenant_id, s.created_at ASC
            ON CONFLICT (tenant_id) DO NOTHING
            """
        )
    )


def downgrade() -> None:
    op.execute(sa.text("DROP TABLE IF EXISTS public.merchant_business_profiles"))
    for col in ("whatsapp_phone", "first_product_at", "first_commission_at"):
        op.execute(
            sa.text(f"ALTER TABLE public.merchant_leads DROP COLUMN IF EXISTS {col}")
        )
    op.execute(sa.text("ALTER TABLE public.users DROP COLUMN IF EXISTS whatsapp_phone"))
