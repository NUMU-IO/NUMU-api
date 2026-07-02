"""Add TikTok tracking foundation: tiktok_event_log + service enum extension.

Phase 1 (P0+P1 data layer) of the TikTok (Pixel + Events API) integration.
Foundational only — mirrors ``20260427_add_meta_tracking``. Subsequent
phases (Celery task, /track fan-out, settings routes, frontend) ride on
top of the schema landed here.

What this does:

  1. Extends ``service_name_enum`` with ``tiktok_capi`` so
     ``ServiceCredential`` rows can carry TikTok Events API access tokens
     via the existing encrypted-credential pattern:

        service_type = TRACKING          (already added by the Meta migration)
        service_name = TIKTOK_CAPI

     ``ALTER TYPE … ADD VALUE`` (not a new enum) leaves existing rows
     untouched.

  2. Creates ``public.tiktok_event_log`` — append-only audit + idempotency
     log for every Events API event the platform sends (or attempts). The
     ``UNIQUE (store_id, event_id)`` constraint is the **server-side dedup
     primitive**; the Celery task relies on the ``IntegrityError`` raised
     by a duplicate insert as its "skip, already sent" signal. Unlike the
     Meta table it also carries ``response_code`` (TikTok answers HTTP 200
     even on logical errors and puts the real result in the body ``code``).

  3. Backfills ``store.settings.tracking.tiktok`` from any legacy flat
     ``store.settings.tiktok_pixel_id`` field, landing those stores in
     **Pixel-only** mode (``pixel_enabled = true``, ``api_enabled =
     false``) — behaviour-preserving for any store that had a TikTok pixel
     set before this integration.

Revision ID: tiktok_tracking_20260701
Revises: merge_variant_risk_20260731
Create Date: 2026-07-01
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "tiktok_tracking_20260701"
down_revision: str | None = "merge_variant_risk_20260731"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # ------------------------------------------------------------------ #
    # 1. Extend service_name_enum                                         #
    #                                                                    #
    # ``service_type_enum`` already gained 'tracking' via the Meta       #
    # migration, so only the service NAME needs the new member. The new  #
    # value is NOT used elsewhere in this migration (the event-log table #
    # doesn't reference the enum), so ADD VALUE inside Alembic's tx is   #
    # safe — same as the Meta migration.                                 #
    # ------------------------------------------------------------------ #
    op.execute("ALTER TYPE public.service_type_enum ADD VALUE IF NOT EXISTS 'tracking'")
    op.execute(
        "ALTER TYPE public.service_name_enum ADD VALUE IF NOT EXISTS 'tiktok_capi'"
    )

    # ------------------------------------------------------------------ #
    # 2. Create tiktok_event_log table                                   #
    # ------------------------------------------------------------------ #
    op.create_table(
        "tiktok_event_log",
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
        # Shared with the browser-side ttq.track() fire — TikTok dedupes on
        # (pixel_id, event, event_id). TEXT (not UUID) because non-purchase
        # events use synthesized non-UUID IDs.
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
        # TikTok business-level result code (0 == OK).
        sa.Column("response_code", sa.Integer(), nullable=True),
        sa.Column(
            "response_body",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
        sa.Column("request_id", sa.Text(), nullable=True),
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
        sa.UniqueConstraint(
            "store_id", "event_id", name="uq_tiktok_event_log_store_event_id"
        ),
        schema="public",
    )

    op.create_index(
        "ix_tiktok_event_log_tenant_id",
        "tiktok_event_log",
        ["tenant_id"],
        schema="public",
    )
    op.create_index(
        "ix_tiktok_event_log_store_id",
        "tiktok_event_log",
        ["store_id"],
        schema="public",
    )
    op.create_index(
        "idx_tiktok_event_log_store_event",
        "tiktok_event_log",
        ["store_id", "event_name", sa.text("created_at DESC")],
        schema="public",
    )
    op.create_index(
        "idx_tiktok_event_log_failed",
        "tiktok_event_log",
        ["store_id"],
        schema="public",
        postgresql_where=sa.text(
            "response_status >= 400 OR response_status IS NULL OR response_code <> 0"
        ),
    )

    # ------------------------------------------------------------------ #
    # 3. Row-Level Security (mirror meta_event_log)                       #
    # ------------------------------------------------------------------ #
    conn = op.get_bind()

    conn.exec_driver_sql(
        "ALTER TABLE public.tiktok_event_log ENABLE ROW LEVEL SECURITY"
    )
    conn.exec_driver_sql("ALTER TABLE public.tiktok_event_log FORCE ROW LEVEL SECURITY")

    conn.exec_driver_sql(
        """
        CREATE POLICY tenant_isolation_select
        ON public.tiktok_event_log
        FOR SELECT
        USING (tenant_id = public.get_current_tenant_id())
        """
    )
    conn.exec_driver_sql(
        """
        CREATE POLICY tenant_isolation_insert
        ON public.tiktok_event_log
        FOR INSERT
        WITH CHECK (tenant_id = public.get_current_tenant_id())
        """
    )
    conn.exec_driver_sql(
        """
        CREATE POLICY tenant_isolation_update
        ON public.tiktok_event_log
        FOR UPDATE
        USING (tenant_id = public.get_current_tenant_id())
        WITH CHECK (tenant_id = public.get_current_tenant_id())
        """
    )
    conn.exec_driver_sql(
        """
        CREATE POLICY tenant_isolation_delete
        ON public.tiktok_event_log
        FOR DELETE
        USING (tenant_id = public.get_current_tenant_id())
        """
    )
    conn.exec_driver_sql(
        """
        CREATE POLICY admin_bypass
        ON public.tiktok_event_log
        FOR ALL
        USING (public.is_rls_bypassed())
        WITH CHECK (public.is_rls_bypassed())
        """
    )

    # ------------------------------------------------------------------ #
    # 4. Backfill store.settings.tracking.tiktok from legacy flat field   #
    #                                                                    #
    # Any store with a legacy ``settings.tiktok_pixel_id`` lands in       #
    # Pixel-only mode. The legacy flat field is left in place (frontend   #
    # reads the new path first, falls back to old).                       #
    # ------------------------------------------------------------------ #
    conn.execute(
        sa.text(
            """
            UPDATE public.stores
            SET settings = jsonb_set(
                COALESCE(settings, '{}'::jsonb),
                '{tracking,tiktok}',
                jsonb_build_object(
                    'pixel_id', settings ->> 'tiktok_pixel_id',
                    'pixel_enabled', true,
                    'api_enabled', false
                ),
                true
            )
            WHERE settings ? 'tiktok_pixel_id'
              AND settings ->> 'tiktok_pixel_id' IS NOT NULL
              AND settings ->> 'tiktok_pixel_id' <> ''
              AND NOT (
                  COALESCE(settings -> 'tracking' -> 'tiktok', '{}'::jsonb)
                      ? 'pixel_id'
              )
            """
        )
    )

    # ------------------------------------------------------------------ #
    # 5. Seed platform-wide feature flag (default OFF)                    #
    # ------------------------------------------------------------------ #
    conn.execute(
        sa.text(
            """
            INSERT INTO public.platform_config (key, value, description)
            VALUES (
                'tiktok_tracking',
                '{"tiktok_capi_enabled_global": false}'::jsonb,
                'Global feature flags for TikTok Pixel + Events API integration. '
                'tiktok_capi_enabled_global gates server-side Events API fan-out '
                'platform-wide; flipped to true at GA, removed after 30 days at 100%.'
            )
            ON CONFLICT (key) DO NOTHING
            """
        )
    )


def downgrade() -> None:
    """Reverse the schema changes (best-effort).

    Notes:
      * Postgres can't drop enum values; the new TIKTOK_CAPI member
        remains after downgrade. Harmless — no rows reference it once the
        credential rows are gone.
      * The ``tracking.tiktok`` JSON sub-object is removed from
        store.settings; the legacy ``tiktok_pixel_id`` flat field is
        untouched.
    """
    conn = op.get_bind()

    conn.execute(
        sa.text("DELETE FROM public.platform_config WHERE key = 'tiktok_tracking'")
    )

    conn.execute(
        sa.text(
            """
            UPDATE public.stores
            SET settings = settings #- '{tracking,tiktok}'
            WHERE settings -> 'tracking' ? 'tiktok'
            """
        )
    )

    for policy in (
        "admin_bypass",
        "tenant_isolation_delete",
        "tenant_isolation_update",
        "tenant_isolation_insert",
        "tenant_isolation_select",
    ):
        conn.exec_driver_sql(
            f"DROP POLICY IF EXISTS {policy} ON public.tiktok_event_log"
        )
    conn.exec_driver_sql(
        "ALTER TABLE public.tiktok_event_log DISABLE ROW LEVEL SECURITY"
    )

    op.drop_index(
        "idx_tiktok_event_log_failed",
        table_name="tiktok_event_log",
        schema="public",
    )
    op.drop_index(
        "idx_tiktok_event_log_store_event",
        table_name="tiktok_event_log",
        schema="public",
    )
    op.drop_index(
        "ix_tiktok_event_log_store_id",
        table_name="tiktok_event_log",
        schema="public",
    )
    op.drop_index(
        "ix_tiktok_event_log_tenant_id",
        table_name="tiktok_event_log",
        schema="public",
    )
    op.drop_table("tiktok_event_log", schema="public")

    # Enum member is not dropped — Postgres limitation.
