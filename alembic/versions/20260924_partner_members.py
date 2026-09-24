"""Partner portal: team members and app uninstall events.

Revision ID: partner_members_20260924
Revises: kashier_card_token_20260923
Create Date: 2026-09-24

Additive, two new public tables, no RLS (like ``partner_accounts``: every
read is scoped by the partner in the route):
- ``partner_members``: teammates on a partner account. The account owner is
  ``partner_accounts.user_id`` and has no row, so no backfill;
- ``app_uninstall_events``: one row per Partner App uninstall, for the
  partner dashboard (the installation row is deleted at uninstall).

Idempotent (IF NOT EXISTS). Downgrade drops both tables.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "partner_members_20260924"
down_revision: str | Sequence[str] | None = "kashier_card_token_20260923"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS public.partner_members (
            id UUID PRIMARY KEY,
            partner_id UUID NOT NULL
                REFERENCES public.partner_accounts(id) ON DELETE CASCADE,
            user_id UUID UNIQUE REFERENCES public.users(id) ON DELETE CASCADE,
            email VARCHAR(255) NOT NULL,
            role VARCHAR(20) NOT NULL,
            invited_by UUID REFERENCES public.users(id) ON DELETE SET NULL,
            status VARCHAR(20) NOT NULL DEFAULT 'invited',
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            CONSTRAINT ck_partner_members_role
                CHECK (role IN ('owner', 'admin', 'developer')),
            CONSTRAINT ck_partner_members_status
                CHECK (status IN ('invited', 'active')),
            CONSTRAINT uq_partner_members_partner_email UNIQUE (partner_id, email)
        )
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_partner_members_email "
        "ON public.partner_members (email)"
    )
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS public.app_uninstall_events (
            id UUID PRIMARY KEY,
            app_id UUID NOT NULL REFERENCES public.apps(id) ON DELETE CASCADE,
            store_id UUID NOT NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_app_uninstall_events_app_created "
        "ON public.app_uninstall_events (app_id, created_at)"
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS public.app_uninstall_events")
    op.execute("DROP TABLE IF EXISTS public.partner_members")
