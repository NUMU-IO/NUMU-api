"""Add Meta tracking foundation: meta_event_log + service enum extensions.

Phase 1 of the Meta (Pixel + Conversions API) integration. This migration
is foundational only — it does NOT wire any code paths. Subsequent phases
(Celery task, /track fan-out, settings routes, frontend) ride on top of
the schema landed here.

What this does:

  1. Creates ``public.meta_event_log`` — append-only audit + idempotency
     log for every CAPI event the platform sends (or attempts). The
     ``UNIQUE (store_id, event_id)`` constraint is the **server-side
     dedup primitive**; the Phase-2 Celery task relies on the
     ``IntegrityError`` raised by a duplicate insert as its "skip,
     already sent" signal.

  2. Extends two existing Postgres enums (``service_type_enum`` and
     ``service_name_enum``) so ``ServiceCredential`` rows can carry
     CAPI access tokens via the existing encrypted-credential pattern
     used for Paymob / Fawry / Bosta / WhatsApp:

        service_type = TRACKING
        service_name = META_CAPI

     We use ``ALTER TYPE … ADD VALUE`` (not a new enum) so existing
     credential rows are untouched.

  3. Backfills ``store.settings.tracking.meta`` from the legacy flat
     ``store.settings.meta_pixel_id`` field. Stores that already had a
     pixel configured land in **Mode A (Pixel only)** — i.e.
     ``pixel_enabled = true``, ``capi_enabled = false`` — which is the
     intended default per plan §11.2 / §2.4. The legacy flat field is
     left in place for backward compatibility (frontend reads new path
     first, falls back to old) and will be dropped in a follow-up
     migration after two release cycles.

Revision ID: meta_tracking_20260427
Revises: merge_heads_20260426
Create Date: 2026-04-27
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "meta_tracking_20260427"
down_revision: str | None = "merge_heads_20260426"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # ------------------------------------------------------------------ #
    # 1. Extend existing service enums                                    #
    #                                                                     #
    # Postgres requires ADD VALUE statements to be committed outside any  #
    # transaction that uses the new value. Alembic's per-migration tx is  #
    # fine because no later step in this file references these values.    #
    # ------------------------------------------------------------------ #
    op.execute("ALTER TYPE public.service_type_enum ADD VALUE IF NOT EXISTS 'tracking'")
    op.execute(
        "ALTER TYPE public.service_name_enum ADD VALUE IF NOT EXISTS 'meta_capi'"
    )

    # ------------------------------------------------------------------ #
    # 2. Create meta_event_log table                                      #
    # ------------------------------------------------------------------ #
    op.create_table(
        "meta_event_log",
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
        # Shared with the browser-side Pixel fire — Meta dedupes on
        # (pixel_id, event_name, event_id). NB: TEXT (not UUID) because
        # non-Purchase events use synthesized non-UUID IDs (e.g.
        # "<productId>-<sessionId>" for ViewContent, sha256 prefix for
        # InitiateCheckout). Purchase still uses order.id verbatim.
        sa.Column("event_id", sa.Text(), nullable=False),
        sa.Column("event_name", sa.Text(), nullable=False),
        sa.Column("event_time", sa.DateTime(timezone=True), nullable=False),
        sa.Column("pixel_id", sa.Text(), nullable=False),
        sa.Column(
            "request_payload",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
        ),
        sa.Column("response_status", sa.Integer(), nullable=True),
        sa.Column(
            "response_body",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
        sa.Column("fbtrace_id", sa.Text(), nullable=True),
        sa.Column(
            "attempt_count",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("1"),
        ),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        # Server-side dedup primitive — Phase 2's Celery task INSERTs
        # this row first; an IntegrityError means the event was already
        # sent and the task short-circuits without contacting Meta.
        sa.UniqueConstraint(
            "store_id", "event_id", name="uq_meta_event_log_store_event_id"
        ),
        schema="public",
    )

    op.create_index(
        "ix_meta_event_log_tenant_id",
        "meta_event_log",
        ["tenant_id"],
        schema="public",
    )
    op.create_index(
        "ix_meta_event_log_store_id",
        "meta_event_log",
        ["store_id"],
        schema="public",
    )
    op.create_index(
        "idx_meta_event_log_store_event",
        "meta_event_log",
        ["store_id", "event_name", sa.text("created_at DESC")],
        schema="public",
    )
    # Partial index for the dashboard's "failing" filter and for
    # background sweeps that retry rows stuck without a response.
    op.create_index(
        "idx_meta_event_log_failed",
        "meta_event_log",
        ["store_id"],
        schema="public",
        postgresql_where=sa.text("response_status >= 400 OR response_status IS NULL"),
    )

    # ------------------------------------------------------------------ #
    # 3. Row-Level Security (mirror message_logs pattern)                 #
    # ------------------------------------------------------------------ #
    conn = op.get_bind()

    conn.exec_driver_sql("ALTER TABLE public.meta_event_log ENABLE ROW LEVEL SECURITY")
    conn.exec_driver_sql("ALTER TABLE public.meta_event_log FORCE ROW LEVEL SECURITY")

    conn.exec_driver_sql(
        """
        CREATE POLICY tenant_isolation_select
        ON public.meta_event_log
        FOR SELECT
        USING (tenant_id = public.get_current_tenant_id())
        """
    )
    conn.exec_driver_sql(
        """
        CREATE POLICY tenant_isolation_insert
        ON public.meta_event_log
        FOR INSERT
        WITH CHECK (tenant_id = public.get_current_tenant_id())
        """
    )
    conn.exec_driver_sql(
        """
        CREATE POLICY tenant_isolation_update
        ON public.meta_event_log
        FOR UPDATE
        USING (tenant_id = public.get_current_tenant_id())
        WITH CHECK (tenant_id = public.get_current_tenant_id())
        """
    )
    conn.exec_driver_sql(
        """
        CREATE POLICY tenant_isolation_delete
        ON public.meta_event_log
        FOR DELETE
        USING (tenant_id = public.get_current_tenant_id())
        """
    )
    conn.exec_driver_sql(
        """
        CREATE POLICY admin_bypass
        ON public.meta_event_log
        FOR ALL
        USING (public.is_rls_bypassed())
        WITH CHECK (public.is_rls_bypassed())
        """
    )

    # ------------------------------------------------------------------ #
    # 4. Backfill store.settings.tracking.meta from legacy field          #
    #                                                                     #
    # For every store with a legacy `settings.meta_pixel_id`, ensure      #
    # `settings.tracking.meta.{pixel_id, pixel_enabled}` is populated.    #
    # Mode A (Pixel only) is the default for these legacy stores — the    #
    # frontend was already firing Pixel events for them, so flipping      #
    # `pixel_enabled = true` preserves behavior. They opt into CAPI       #
    # later by adding a token from the dashboard.                         #
    #                                                                     #
    # The legacy `meta_pixel_id` field is intentionally left in place     #
    # for two release cycles (back-compat per plan §3.2). A follow-up     #
    # migration will drop it.                                             #
    # ------------------------------------------------------------------ #
    conn.execute(
        sa.text(
            """
            UPDATE public.stores
            SET settings = jsonb_set(
                COALESCE(settings, '{}'::jsonb),
                '{tracking,meta}',
                jsonb_build_object(
                    'pixel_id', settings ->> 'meta_pixel_id',
                    'pixel_enabled', true,
                    'capi_enabled', false
                ),
                true
            )
            WHERE settings ? 'meta_pixel_id'
              AND settings ->> 'meta_pixel_id' IS NOT NULL
              AND settings ->> 'meta_pixel_id' <> ''
              AND NOT (
                  COALESCE(settings -> 'tracking' -> 'meta', '{}'::jsonb)
                      ? 'pixel_id'
              )
            """
        )
    )

    # ------------------------------------------------------------------ #
    # 5. Seed platform-wide feature flag (default OFF — see plan §11.3)   #
    #                                                                     #
    # `platform_config` is a key/value JSONB table; we add a single       #
    # `meta_tracking` row holding global toggles. CAPI fan-out from       #
    # /track and webhooks (Phase 2) will check this flag in addition to   #
    # the per-store activation booleans.                                  #
    # ------------------------------------------------------------------ #
    conn.execute(
        sa.text(
            """
            INSERT INTO public.platform_config (key, value, description)
            VALUES (
                'meta_tracking',
                '{"meta_capi_enabled_global": false}'::jsonb,
                'Global feature flags for Meta Pixel + Conversions API integration. '
                'meta_capi_enabled_global gates server-side CAPI fan-out platform-wide; '
                'flipped to true at GA per plan §11.3, removed after 30 days at 100%.'
            )
            ON CONFLICT (key) DO NOTHING
            """
        )
    )


def downgrade() -> None:
    """Reverse the schema changes (best-effort).

    Notes:
      * Postgres can't drop enum values; the new TRACKING / META_CAPI
        members remain after downgrade. They're harmless — no rows
        reference them once the credential rows are gone.
      * The `tracking.meta` JSON sub-object is removed from
        store.settings; the legacy `meta_pixel_id` flat field is
        untouched (it was never modified).
    """
    conn = op.get_bind()

    # 5. Drop the platform-wide feature-flag row.
    conn.execute(
        sa.text("DELETE FROM public.platform_config WHERE key = 'meta_tracking'")
    )

    # 4. Strip the backfilled tracking.meta sub-object so a future
    #    re-upgrade can re-run the backfill cleanly.
    conn.execute(
        sa.text(
            """
            UPDATE public.stores
            SET settings = settings #- '{tracking,meta}'
            WHERE settings -> 'tracking' ? 'meta'
            """
        )
    )

    # 3. Drop RLS policies.
    for policy in (
        "admin_bypass",
        "tenant_isolation_delete",
        "tenant_isolation_update",
        "tenant_isolation_insert",
        "tenant_isolation_select",
    ):
        conn.exec_driver_sql(f"DROP POLICY IF EXISTS {policy} ON public.meta_event_log")
    conn.exec_driver_sql("ALTER TABLE public.meta_event_log DISABLE ROW LEVEL SECURITY")

    # 2. Drop indexes + table.
    op.drop_index(
        "idx_meta_event_log_failed",
        table_name="meta_event_log",
        schema="public",
    )
    op.drop_index(
        "idx_meta_event_log_store_event",
        table_name="meta_event_log",
        schema="public",
    )
    op.drop_index(
        "ix_meta_event_log_store_id",
        table_name="meta_event_log",
        schema="public",
    )
    op.drop_index(
        "ix_meta_event_log_tenant_id",
        table_name="meta_event_log",
        schema="public",
    )
    op.drop_table("meta_event_log", schema="public")

    # 1. Enum members are not dropped — Postgres limitation.
