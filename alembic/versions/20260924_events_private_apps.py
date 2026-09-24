"""Private (custom) Partner Apps: bind an app to one store.

Revision ID: events_private_20260924
Revises: partner_members_20260924
Create Date: 2026-09-24

Additive: ``apps.private_store_id``. NULL for every existing app (public).
A private app installs only on that store, is never listed in the App
Store, skips review and cannot be billed by NUMU. Deleting the store
deletes its private apps.

Idempotent (IF NOT EXISTS). Downgrade drops the column.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "events_private_20260924"
down_revision: str | Sequence[str] | None = "partner_members_20260924"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE public.apps ADD COLUMN IF NOT EXISTS private_store_id UUID "
        "REFERENCES public.stores(id) ON DELETE CASCADE"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_apps_private_store "
        "ON public.apps (private_store_id) WHERE private_store_id IS NOT NULL"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS public.ix_apps_private_store")
    op.execute("ALTER TABLE public.apps DROP COLUMN IF EXISTS private_store_id")
