"""Merchant leads table — acquisition records that outlive the tenant.

Lead details lived on ``tenants`` (demo_name / demo_email / demo_whatsapp)
and the demo cleanup task hard-deletes expired demo tenants every two
hours, taking the lead with them. This table holds the same information
independently, plus the attribution fields (UTMs, referrer, landing path)
that were never captured anywhere.

No foreign keys to ``tenants`` or ``users`` on purpose: a cascade would
reintroduce the deletion this table exists to survive. ``tenant_id`` and
``user_id`` are plain UUIDs and are expected to dangle.

Also adds ``users.plan_applied_at``. Store creation used to record "this
plan intent has been acted on" by nulling ``users.plan_intent`` — which
destroyed the only acquisition signal we had at the exact moment it
became interesting. A timestamp is the re-run guard now and the intent
is kept.

Backfills the demo leads that still exist at deploy time. Demo tenants
already cleaned up are unrecoverable — this is the last chance to keep
the ones still in the table.

Revision ID: merchant_leads_20260902
Revises: tenant_founder_cohort_20260828
"""

import sqlalchemy as sa

from alembic import op

revision = "merchant_leads_20260902"
down_revision = "tenant_founder_cohort_20260828"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # IF NOT EXISTS throughout: prod has picked up hand-created objects
    # before, and a migration that cannot be re-run blocks a deploy at
    # the worst possible moment.
    op.execute(
        sa.text(
            """
            CREATE TABLE IF NOT EXISTS public.merchant_leads (
                id UUID PRIMARY KEY,
                created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                email VARCHAR(255) NOT NULL,
                name VARCHAR(160),
                phone VARCHAR(20),
                language VARCHAR(5),
                source VARCHAR(32) NOT NULL,
                last_source VARCHAR(32),
                plan_intent VARCHAR(20),
                utm_source VARCHAR(120),
                utm_medium VARCHAR(120),
                utm_campaign VARCHAR(120),
                utm_content VARCHAR(120),
                referrer VARCHAR(500),
                landing_path VARCHAR(255),
                tenant_id UUID,
                user_id UUID,
                store_subdomain VARCHAR(63),
                status VARCHAR(20) NOT NULL DEFAULT 'new',
                demo_started_at TIMESTAMPTZ,
                registered_at TIMESTAMPTZ,
                store_created_at TIMESTAMPTZ,
                first_order_at TIMESTAMPTZ,
                last_seen_at TIMESTAMPTZ,
                notes TEXT
            )
            """
        )
    )
    op.execute(
        sa.text(
            "CREATE UNIQUE INDEX IF NOT EXISTS ix_merchant_leads_email "
            "ON public.merchant_leads (email)"
        )
    )
    op.execute(
        sa.text(
            "CREATE INDEX IF NOT EXISTS ix_merchant_leads_phone "
            "ON public.merchant_leads (phone)"
        )
    )
    op.execute(
        sa.text(
            "CREATE INDEX IF NOT EXISTS ix_merchant_leads_status_created "
            "ON public.merchant_leads (status, created_at)"
        )
    )
    op.execute(
        sa.text(
            "CREATE INDEX IF NOT EXISTS ix_merchant_leads_tenant_id "
            "ON public.merchant_leads (tenant_id)"
        )
    )

    # ── Backfill ──────────────────────────────────────────────────
    # Existing demo tenants still carry their lead on the tenant row.
    # ON CONFLICT DO NOTHING because a person may appear on several demo
    # tenants; the earliest row wins, which matches "first touch".
    op.execute(
        sa.text(
            """
            INSERT INTO public.merchant_leads (
                id, created_at, updated_at, email, name, phone,
                source, last_source, tenant_id, store_subdomain,
                status, demo_started_at, last_seen_at
            )
            SELECT
                gen_random_uuid(),
                COALESCE(t.demo_started_at, t.created_at),
                now(),
                lower(t.demo_email),
                t.demo_name,
                t.demo_whatsapp,
                'demo',
                'demo',
                t.id,
                t.subdomain,
                'demo_started',
                t.demo_started_at,
                COALESCE(t.demo_started_at, t.created_at)
            FROM public.tenants t
            WHERE t.demo_email IS NOT NULL
            ORDER BY COALESCE(t.demo_started_at, t.created_at) ASC
            ON CONFLICT (email) DO NOTHING
            """
        )
    )

    # Real merchants already on the platform. They have no attribution —
    # nothing ever recorded it — but a lead row for every existing owner
    # means sales works from one table instead of two.
    op.execute(
        sa.text(
            """
            INSERT INTO public.merchant_leads (
                id, created_at, updated_at, email, name, phone,
                source, last_source, plan_intent, user_id,
                status, registered_at, last_seen_at
            )
            SELECT
                gen_random_uuid(),
                u.created_at,
                now(),
                lower(u.email),
                NULLIF(TRIM(CONCAT(u.first_name, ' ', u.last_name)), ''),
                u.phone,
                'signup',
                'signup',
                u.plan_intent,
                u.id,
                'registered',
                u.created_at,
                COALESCE(u.last_login_at, u.created_at)
            FROM public.users u
            WHERE u.role::text IN ('STORE_OWNER', 'store_owner')
              -- Ephemeral demo owners are not leads. The real person
              -- behind a demo is captured from tenants.demo_email above.
              AND u.email NOT LIKE '%@demo.numu.local'
            ORDER BY u.created_at ASC
            ON CONFLICT (email) DO NOTHING
            """
        )
    )

    # ── users.plan_applied_at ─────────────────────────────────────
    op.execute(
        sa.text(
            "ALTER TABLE public.users "
            "ADD COLUMN IF NOT EXISTS plan_applied_at TIMESTAMPTZ"
        )
    )
    # Merchants already on payg had their intent consumed and nulled, so
    # there is no intent left to re-apply. Stamping them keeps the new
    # "already applied" guard correct for anyone who creates a second
    # store after this deploy.
    op.execute(
        sa.text(
            """
            UPDATE public.users u
            SET plan_applied_at = t.created_at
            FROM public.tenants t
            WHERE t.owner_id = u.id
              AND t.plan = 'payg'
              AND u.plan_applied_at IS NULL
            """
        )
    )

    # Promote backfilled leads that already own a store, so the funnel
    # column is honest from day one rather than only for new signups.
    op.execute(
        sa.text(
            """
            UPDATE public.merchant_leads l
            SET status = 'store_created',
                tenant_id = COALESCE(l.tenant_id, t.id),
                store_subdomain = COALESCE(l.store_subdomain, t.subdomain),
                store_created_at = t.created_at
            FROM public.tenants t
            WHERE t.owner_id = l.user_id
              AND l.status = 'registered'
            """
        )
    )


def downgrade() -> None:
    op.execute(
        sa.text("ALTER TABLE public.users DROP COLUMN IF EXISTS plan_applied_at")
    )
    op.execute(sa.text("DROP TABLE IF EXISTS public.merchant_leads"))
