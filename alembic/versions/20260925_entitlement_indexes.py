"""Indexes for entitlement history and the WhatsApp allowance count.

Revision ID: entitlement_indexes_20260925
Revises: entitlements_20260925
Create Date: 2026-09-25

* ``ix_audit_logs_resource``: the admin History tabs read audit_logs by
  resource; only event type, tenant, user, store and time were indexed.
* ``ix_message_logs_store_template_created``: whatsapp_entitlement counts a
  store's template sends since its period start on every send and on the
  checkout OTP check, and only ``store_id`` was indexed, so the count walked
  the store's whole history.

Both tables take writes all day, so the indexes are built ``CONCURRENTLY``,
which Postgres refuses inside a transaction: commit Alembic's first.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "entitlement_indexes_20260925"
down_revision: str | None = "entitlements_20260925"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("COMMIT")
    op.execute(
        "CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_audit_logs_resource"
        " ON public.audit_logs (resource_type, resource_id, created_at)"
    )
    op.execute(
        "CREATE INDEX CONCURRENTLY IF NOT EXISTS"
        " ix_message_logs_store_template_created"
        " ON public.message_logs (store_id, created_at)"
        " WHERE template_name IS NOT NULL"
    )


def downgrade() -> None:
    op.execute("COMMIT")
    for name in ("ix_audit_logs_resource", "ix_message_logs_store_template_created"):
        op.execute(f"DROP INDEX CONCURRENTLY IF EXISTS public.{name}")
