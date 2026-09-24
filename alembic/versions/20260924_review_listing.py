"""App review rounds, app listings, partner notifications.

Revision ID: review_listing_20260924
Revises: partner_members_20260924
Create Date: 2026-09-24

Additive, three new public tables, no RLS (every read is scoped by the
partner or the admin gate in the route, like ``app_versions``):
- ``app_listings``: a Partner App's listing (name, tagline, description,
  screenshots, video, category, keywords), reviewed apart from the manifest;
- ``app_reviews``: one row per review round of a version and/or a listing,
  with the checklist, partner-visible notes and a staff-only note;
- ``partner_notifications``: the partner portal's notification feed.

Backfills one review round per already-submitted version so the queue and
the partner's timeline keep the reviews made before this migration.

Idempotent (IF NOT EXISTS, NOT EXISTS backfill). Downgrade drops the tables.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "review_listing_20260924"
down_revision: str | Sequence[str] | None = "partner_members_20260924"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS public.app_listings (
            id UUID PRIMARY KEY,
            app_id UUID NOT NULL REFERENCES public.apps(id) ON DELETE CASCADE,
            status VARCHAR(24) NOT NULL DEFAULT 'draft',
            content JSONB NOT NULL,
            version_id UUID REFERENCES public.app_versions(id) ON DELETE SET NULL,
            submitted_at TIMESTAMPTZ,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_app_listings_app_status "
        "ON public.app_listings (app_id, status)"
    )
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS public.app_reviews (
            id UUID PRIMARY KEY,
            app_id UUID NOT NULL REFERENCES public.apps(id) ON DELETE CASCADE,
            version_id UUID REFERENCES public.app_versions(id) ON DELETE CASCADE,
            listing_id UUID REFERENCES public.app_listings(id) ON DELETE CASCADE,
            round INTEGER NOT NULL DEFAULT 1,
            status VARCHAR(24) NOT NULL DEFAULT 'submitted',
            checklist JSONB,
            notes JSONB,
            internal_note TEXT,
            reviewer_id UUID,
            submitted_at TIMESTAMPTZ NOT NULL,
            decided_at TIMESTAMPTZ,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_app_reviews_status_submitted "
        "ON public.app_reviews (status, submitted_at)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_app_reviews_app ON public.app_reviews (app_id)"
    )
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS public.partner_notifications (
            id UUID PRIMARY KEY,
            partner_id UUID NOT NULL
                REFERENCES public.partner_accounts(id) ON DELETE CASCADE,
            kind VARCHAR(40) NOT NULL,
            data JSONB NOT NULL DEFAULT '{}'::jsonb,
            app_id UUID REFERENCES public.apps(id) ON DELETE CASCADE,
            link VARCHAR(512),
            read_at TIMESTAMPTZ,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_partner_notifications_partner_created "
        "ON public.partner_notifications (partner_id, created_at)"
    )
    op.execute(
        """
        INSERT INTO public.app_reviews (
            id, app_id, version_id, round, status, checklist, notes,
            reviewer_id, submitted_at, decided_at
        )
        SELECT
            gen_random_uuid(), v.app_id, v.id,
            row_number() OVER (PARTITION BY v.app_id ORDER BY v.submitted_at),
            CASE WHEN v.status = 'published' THEN 'approved' ELSE v.status END,
            v.review_checklist, v.review_notes, v.reviewed_by, v.submitted_at,
            CASE WHEN v.status IN ('submitted', 'in_review') THEN NULL
                 ELSE v.reviewed_at END
        FROM public.app_versions v
        WHERE v.submitted_at IS NOT NULL
          AND v.status IN ('submitted', 'in_review', 'approved',
                           'changes_requested', 'rejected', 'published')
          AND NOT EXISTS (
              SELECT 1 FROM public.app_reviews r WHERE r.version_id = v.id
          )
        """
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS public.partner_notifications")
    op.execute("DROP TABLE IF EXISTS public.app_reviews")
    op.execute("DROP TABLE IF EXISTS public.app_listings")
