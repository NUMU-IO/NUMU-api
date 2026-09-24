"""App reviews and support threads.

Revision ID: reviews_support_20260924
Revises: partner_members_20260924
Create Date: 2026-09-24

Additive, public tables with no RLS (like ``partner_members``: every read is
scoped in the route by store, partner or admin):
- ``app_reviews``: one rating per store per app, the partner's reply and
  the moderation state;
- ``support_tickets`` / ``support_messages``: merchant to partner and
  partner to NUMU threads;
- ``app_uninstall_events.installed_at``: how long the uninstalled app was
  installed, for review eligibility.

Idempotent (IF NOT EXISTS). Downgrade drops what it added.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "reviews_support_20260924"
down_revision: str | Sequence[str] | None = "partner_members_20260924"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE public.app_uninstall_events "
        "ADD COLUMN IF NOT EXISTS installed_at TIMESTAMPTZ"
    )
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS public.app_reviews (
            id UUID PRIMARY KEY,
            app_id UUID NOT NULL REFERENCES public.apps(id) ON DELETE CASCADE,
            store_id UUID NOT NULL REFERENCES public.stores(id) ON DELETE CASCADE,
            user_id UUID REFERENCES public.users(id) ON DELETE SET NULL,
            rating INTEGER NOT NULL,
            body TEXT,
            reply_body TEXT,
            replied_at TIMESTAMPTZ,
            is_hidden BOOLEAN NOT NULL DEFAULT false,
            reported_at TIMESTAMPTZ,
            report_reason VARCHAR(500),
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            CONSTRAINT uq_app_reviews_app_store UNIQUE (app_id, store_id),
            CONSTRAINT ck_app_reviews_rating CHECK (rating BETWEEN 1 AND 5)
        )
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_app_reviews_app_created "
        "ON public.app_reviews (app_id, created_at)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_app_reviews_reported "
        "ON public.app_reviews (reported_at) WHERE reported_at IS NOT NULL"
    )
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS public.support_tickets (
            id UUID PRIMARY KEY,
            kind VARCHAR(20) NOT NULL,
            app_id UUID REFERENCES public.apps(id) ON DELETE CASCADE,
            store_id UUID REFERENCES public.stores(id) ON DELETE CASCADE,
            partner_id UUID REFERENCES public.partner_accounts(id) ON DELETE CASCADE,
            opened_by UUID REFERENCES public.users(id) ON DELETE SET NULL,
            subject VARCHAR(200) NOT NULL,
            status VARCHAR(20) NOT NULL DEFAULT 'open',
            last_message_at TIMESTAMPTZ,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            CONSTRAINT ck_support_tickets_kind CHECK (kind IN ('app', 'partner')),
            CONSTRAINT ck_support_tickets_status
                CHECK (status IN ('open', 'answered', 'closed'))
        )
        """
    )
    for column in ("store_id", "app_id", "partner_id"):
        op.execute(
            f"CREATE INDEX IF NOT EXISTS ix_support_tickets_{column.removesuffix('_id')} "
            f"ON public.support_tickets ({column})"
        )
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS public.support_messages (
            id UUID PRIMARY KEY,
            ticket_id UUID NOT NULL
                REFERENCES public.support_tickets(id) ON DELETE CASCADE,
            author_id UUID REFERENCES public.users(id) ON DELETE SET NULL,
            author_role VARCHAR(20) NOT NULL,
            body TEXT NOT NULL,
            attachments JSONB NOT NULL DEFAULT '[]'::jsonb,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            CONSTRAINT ck_support_messages_author_role
                CHECK (author_role IN ('merchant', 'partner', 'staff'))
        )
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_support_messages_ticket_created "
        "ON public.support_messages (ticket_id, created_at)"
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS public.support_messages")
    op.execute("DROP TABLE IF EXISTS public.support_tickets")
    op.execute("DROP TABLE IF EXISTS public.app_reviews")
    op.execute(
        "ALTER TABLE public.app_uninstall_events DROP COLUMN IF EXISTS installed_at"
    )
