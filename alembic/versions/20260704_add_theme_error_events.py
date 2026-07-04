"""Add theme_error_events table - durable shopper-side bundle-crash telemetry.

Phase 3 moat item: persist the client-side theme bundle errors the storefront
beacon reports (``POST /storefront/store/{store_id}/theme-error``) so crashes
can be queried per theme version and correlated to a publish ("version X
spiked right after we shipped it").

What this does:

  1. Creates ``public.theme_error_events`` — append-only, tenant-scoped
     (``tenant_id`` discriminator, same pattern as page_views / funnel_events).
     Columns are deliberately compact; ``message`` is TEXT (truncated at the
     app layer) and ``occurred_at`` is the server-stamped event time keyed by
     the read index.

  2. Indexes ``(store_id, theme_version, occurred_at)`` — the exact shape of
     the merchant summary query (per-version counts + last_seen over a window,
     scoped to a store) — plus single-column ``store_id`` / ``tenant_id``
     indexes mirroring the ORM model's ``index=True`` columns.

  3. Row-Level Security, ENABLED but intentionally NOT ``FORCE``d:

       * SELECT/UPDATE/DELETE are tenant-isolated via
         ``public.get_current_tenant_id()`` (the merchant read side).
       * INSERT is permissive (``WITH CHECK (true)``). The ingest endpoint is
         PUBLIC and unauthenticated, so no tenant GUC is set on that request;
         a tenant-restricted INSERT check would reject the best-effort beacon
         write. The row is a trusted server-side write — ``tenant_id`` is
         stamped from the resolved store, never chosen by the shopper — so a
         permissive INSERT is safe.
       * ``admin_bypass`` covers the RLS-bypass / admin session path.

     Not forcing RLS keeps the owner/bypass role (prod app role — RLS is
     dormant there) able to insert unimpeded, matching the funnel_events
     precedent while still isolating reads for any non-owner role.

Revision ID: theme_error_events_20260704
Revises: tiktok_shop_20260701
Create Date: 2026-07-04
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "theme_error_events_20260704"
down_revision: str | None = "tiktok_shop_20260701"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # ------------------------------------------------------------------ #
    # 1. Create theme_error_events table                                 #
    # ------------------------------------------------------------------ #
    op.create_table(
        "theme_error_events",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column(
            "tenant_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("public.tenants.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "store_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("public.stores.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("theme_slug", sa.String(length=255), nullable=True),
        sa.Column("theme_version", sa.String(length=50), nullable=True),
        sa.Column("bundle_url", sa.String(length=500), nullable=True),
        # Truncated at the app layer — TEXT so a long stack is never rejected
        # on a DB length constraint.
        sa.Column("message", sa.Text(), nullable=False),
        sa.Column("url", sa.String(length=2000), nullable=True),
        # Event time — server-stamped at ingest (the beacon carries no client
        # clock). Distinct from created_at (row-insert time).
        sa.Column(
            "occurred_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        schema="public",
    )

    op.create_index(
        "ix_theme_error_events_tenant_id",
        "theme_error_events",
        ["tenant_id"],
        schema="public",
    )
    op.create_index(
        "ix_theme_error_events_store_id",
        "theme_error_events",
        ["store_id"],
        schema="public",
    )
    # Serves the merchant summary read: per-version counts + last_seen over a
    # window, scoped to a store.
    op.create_index(
        "ix_theme_error_events_store_version_occurred",
        "theme_error_events",
        ["store_id", "theme_version", "occurred_at"],
        schema="public",
    )

    # ------------------------------------------------------------------ #
    # 2. Row-Level Security (ENABLE, not FORCE — see module docstring)    #
    # ------------------------------------------------------------------ #
    conn = op.get_bind()

    conn.exec_driver_sql(
        "ALTER TABLE public.theme_error_events ENABLE ROW LEVEL SECURITY"
    )

    conn.exec_driver_sql(
        """
        CREATE POLICY tenant_isolation_select
        ON public.theme_error_events
        FOR SELECT
        USING (tenant_id = public.get_current_tenant_id())
        """
    )
    # Permissive INSERT — the public beacon has no tenant context; tenant_id
    # is stamped server-side from the resolved store (never shopper-chosen).
    conn.exec_driver_sql(
        """
        CREATE POLICY allow_public_insert
        ON public.theme_error_events
        FOR INSERT
        WITH CHECK (true)
        """
    )
    conn.exec_driver_sql(
        """
        CREATE POLICY tenant_isolation_update
        ON public.theme_error_events
        FOR UPDATE
        USING (tenant_id = public.get_current_tenant_id())
        WITH CHECK (tenant_id = public.get_current_tenant_id())
        """
    )
    conn.exec_driver_sql(
        """
        CREATE POLICY tenant_isolation_delete
        ON public.theme_error_events
        FOR DELETE
        USING (tenant_id = public.get_current_tenant_id())
        """
    )
    conn.exec_driver_sql(
        """
        CREATE POLICY admin_bypass
        ON public.theme_error_events
        FOR ALL
        USING (public.is_rls_bypassed())
        WITH CHECK (public.is_rls_bypassed())
        """
    )


def downgrade() -> None:
    """Reverse the schema changes."""
    conn = op.get_bind()

    for policy in (
        "admin_bypass",
        "tenant_isolation_delete",
        "tenant_isolation_update",
        "allow_public_insert",
        "tenant_isolation_select",
    ):
        conn.exec_driver_sql(
            f"DROP POLICY IF EXISTS {policy} ON public.theme_error_events"
        )
    conn.exec_driver_sql(
        "ALTER TABLE public.theme_error_events DISABLE ROW LEVEL SECURITY"
    )

    op.drop_index(
        "ix_theme_error_events_store_version_occurred",
        table_name="theme_error_events",
        schema="public",
    )
    op.drop_index(
        "ix_theme_error_events_store_id",
        table_name="theme_error_events",
        schema="public",
    )
    op.drop_index(
        "ix_theme_error_events_tenant_id",
        table_name="theme_error_events",
        schema="public",
    )
    op.drop_table("theme_error_events", schema="public")
