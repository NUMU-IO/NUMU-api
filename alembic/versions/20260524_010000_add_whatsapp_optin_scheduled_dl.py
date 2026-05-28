"""Add WhatsApp opt-ins, scheduled-sends, dead-letters, system templates, RLS

Phase 1 backend foundation for WhatsApp integration (spec:
specs/backend-030-whatsapp-foundation). Three new tenant-scoped tables with
RLS policies, message_log GIN index, and 12 seeded system templates.

Also merges the two existing migration heads (marketing_campaigns_20260722
and is_internal_20260723) into a single line — per project memory
'alembic-sibling-branch-deploy-drift', dual heads are a deploy-drift risk
and a merge migration is the safest way to consolidate them.

Revision ID: wa_optin_sched_dl_20260524
Revises: marketing_campaigns_20260722, is_internal_20260723
Create Date: 2026-05-24
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB, UUID

from alembic import op

revision: str = "wa_optin_sched_dl_20260524"
down_revision: tuple[str, ...] = (
    "marketing_campaigns_20260722",
    "is_internal_20260723",
)
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


# ─── system template seeds (FR-030) ──────────────────────────────────────
# Phase 1 ships these as is_system=true, store_id=NULL (global). They sit
# under NUMU's platform Meta WABA. BYO stores cannot use these templates as-is;
# they must submit their own per FR-026.
_SYSTEM_TEMPLATES: list[dict[str, str]] = [
    # (name, language, category, body_text)
    #
    # Meta validation rules these bodies satisfy (subcode 2388299):
    #   1. Body cannot start with a variable.
    #   2. Body cannot end on a variable — punctuation alone after `{{n}}`
    #      is not enough; there has to be at least one literal word.
    #      i.e. "{{2}}!" is REJECTED, "{{2}}. Thanks!" is accepted.
    #
    # Arabic bodies use Egyptian colloquial dialect (أهلاً يا / استلمنا /
    # شكراً ليك / سايبلك / اتسلم). Language code stays "ar" so backend
    # send-guard lookups in whatsapp_notification_handler.py + the
    # scheduled-send dispatcher continue to resolve.
    (
        "order_confirmation",
        "en",
        "UTILITY",
        "Hi {{1}}, your order {{2}} has been received. Total: {{3}}. Track it here: {{4}} Thank you for shopping with us.",
    ),
    (
        "order_confirmation",
        "ar",
        "UTILITY",
        "أهلاً يا {{1}}، استلمنا طلبك رقم {{2}}. الإجمالي: {{3}}. اتفرج عليه من اللينك ده: {{4}} وشكراً ليك.",
    ),
    (
        "payment_received",
        "en",
        "UTILITY",
        "Payment received for order {{1}}. Amount: {{2}}. Thank you!",
    ),
    (
        "payment_received",
        "ar",
        "UTILITY",
        "استلمنا الدفع لطلبك رقم {{1}}. المبلغ: {{2}}. شكراً ليك!",
    ),
    (
        "order_shipped",
        "en",
        "UTILITY",
        "Your order {{1}} is on the way. Tracking: {{2}} (carrier: {{3}}). Thanks!",
    ),
    (
        "order_shipped",
        "ar",
        "UTILITY",
        "طلبك رقم {{1}} في الطريق! اتابع شحنتك من هنا: {{2}} (مع شركة {{3}}). متشكرين!",
    ),
    (
        "order_delivered",
        "en",
        "UTILITY",
        "Your order {{1}} has been delivered. Thanks for shopping at {{2}}. We hope you enjoy your purchase!",
    ),
    (
        "order_delivered",
        "ar",
        "UTILITY",
        "طلبك رقم {{1}} اتسلم بنجاح. شكراً إنك اتسوقت من {{2}}. نتمنالك تكون مبسوط بشرائك!",
    ),
    (
        "abandoned_cart",
        "en",
        "MARKETING",
        "Hi {{1}}, you left items in your cart at {{2}}. Complete your purchase here: {{3}} before they sell out.",
    ),
    (
        "abandoned_cart",
        "ar",
        "MARKETING",
        "أهلاً يا {{1}}، سايبلك حاجات في عربتك على {{2}}. اكمل الطلب من هنا: {{3}} قبل ما تخلص الكميات.",
    ),
    # STOP-keyword acknowledgement (FR-010). Sent inside the customer-service
    # window (no template approval needed at send-time) but seeded as a
    # template row so the send guard's bypass allowlist (TASK-SEC-010) can
    # look it up by name.
    (
        "optout_confirmation",
        "en",
        "UTILITY",
        "You have been unsubscribed from WhatsApp messages from {{1}}. Reply START to resubscribe.",
    ),
    (
        "optout_confirmation",
        "ar",
        "UTILITY",
        "تم إلغاء اشتراكك من رسايل واتساب بتاعت {{1}}. لو حبيت ترجع تاني، ابعت START.",
    ),
]


def _add_rls_for_table(table_name: str) -> None:
    """Enable RLS on a tenant-scoped table mirroring the central pattern in
    20260203_add_rls_policies.py (4 per-op policies + admin_bypass).
    """
    conn = op.get_bind()

    conn.exec_driver_sql(f"ALTER TABLE public.{table_name} ENABLE ROW LEVEL SECURITY;")
    conn.exec_driver_sql(f"ALTER TABLE public.{table_name} FORCE ROW LEVEL SECURITY;")
    conn.exec_driver_sql(
        f"""
        CREATE POLICY tenant_isolation_select ON public.{table_name}
            FOR SELECT
            USING (tenant_id = public.get_current_tenant_id());
        """
    )
    conn.exec_driver_sql(
        f"""
        CREATE POLICY tenant_isolation_insert ON public.{table_name}
            FOR INSERT
            WITH CHECK (tenant_id = public.get_current_tenant_id());
        """
    )
    conn.exec_driver_sql(
        f"""
        CREATE POLICY tenant_isolation_update ON public.{table_name}
            FOR UPDATE
            USING (tenant_id = public.get_current_tenant_id())
            WITH CHECK (tenant_id = public.get_current_tenant_id());
        """
    )
    conn.exec_driver_sql(
        f"""
        CREATE POLICY tenant_isolation_delete ON public.{table_name}
            FOR DELETE
            USING (tenant_id = public.get_current_tenant_id());
        """
    )
    conn.exec_driver_sql(
        f"""
        CREATE POLICY admin_bypass ON public.{table_name}
            FOR ALL
            USING (public.is_rls_bypassed() = true)
            WITH CHECK (public.is_rls_bypassed() = true);
        """
    )


def _drop_rls_for_table(table_name: str) -> None:
    conn = op.get_bind()
    for policy in (
        "admin_bypass",
        "tenant_isolation_delete",
        "tenant_isolation_update",
        "tenant_isolation_insert",
        "tenant_isolation_select",
    ):
        conn.exec_driver_sql(f"DROP POLICY IF EXISTS {policy} ON public.{table_name};")
    conn.exec_driver_sql(f"ALTER TABLE public.{table_name} DISABLE ROW LEVEL SECURITY;")


def upgrade() -> None:
    # ─── 1. whatsapp_opt_ins ─────────────────────────────────────────
    op.create_table(
        "whatsapp_opt_ins",
        sa.Column(
            "id",
            UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "tenant_id",
            UUID(as_uuid=True),
            sa.ForeignKey("public.tenants.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column(
            "store_id",
            UUID(as_uuid=True),
            sa.ForeignKey("public.stores.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "customer_id",
            UUID(as_uuid=True),
            sa.ForeignKey("public.customers.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("phone", sa.String(20), nullable=False),
        sa.Column("source", sa.String(32), nullable=False),
        sa.Column(
            "opted_in_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("opted_out_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("opt_out_reason", sa.String(64), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.CheckConstraint(
            r"phone ~ '^\+[1-9][0-9]{1,14}$'",
            name="ck_wa_opt_ins_phone_e164",
        ),
        sa.CheckConstraint(
            "source IN ('checkout', 'signup', 'import', 'api', 'inbound_reply')",
            name="ck_wa_opt_ins_source",
        ),
        schema="public",
    )
    # Partial index: primary lookup path is "is this phone actively opted in
    # for this store?" — partial on opted_out_at IS NULL keeps it small.
    op.create_index(
        "ix_wa_opt_ins_store_phone_active",
        "whatsapp_opt_ins",
        ["store_id", "phone"],
        postgresql_where=sa.text("opted_out_at IS NULL"),
        schema="public",
    )
    op.create_index(
        "ix_wa_opt_ins_store_phone_history",
        "whatsapp_opt_ins",
        ["store_id", "phone", "opted_in_at"],
        schema="public",
    )
    op.create_index(
        "ix_wa_opt_ins_customer",
        "whatsapp_opt_ins",
        ["customer_id"],
        postgresql_where=sa.text("customer_id IS NOT NULL"),
        schema="public",
    )
    _add_rls_for_table("whatsapp_opt_ins")

    # ─── 2. whatsapp_scheduled_sends ─────────────────────────────────
    op.create_table(
        "whatsapp_scheduled_sends",
        sa.Column(
            "id",
            UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "tenant_id",
            UUID(as_uuid=True),
            sa.ForeignKey("public.tenants.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column(
            "store_id",
            UUID(as_uuid=True),
            sa.ForeignKey("public.stores.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "customer_id",
            UUID(as_uuid=True),
            sa.ForeignKey("public.customers.id", ondelete="CASCADE"),
            nullable=True,
        ),
        sa.Column("phone", sa.String(20), nullable=False),
        sa.Column(
            "template_id",
            UUID(as_uuid=True),
            sa.ForeignKey("public.whatsapp_templates.id", ondelete="RESTRICT"),
            nullable=True,
        ),
        sa.Column("template_params", JSONB, nullable=True),
        sa.Column("text_message", sa.Text, nullable=True),
        sa.Column("scheduled_for", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "status",
            sa.String(16),
            nullable=False,
            server_default="pending",
        ),
        sa.Column("skip_reason", sa.String(64), nullable=True),
        sa.Column("failure_reason", sa.Text, nullable=True),
        sa.Column(
            "related_order_id",
            UUID(as_uuid=True),
            sa.ForeignKey("public.orders.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "created_by",
            UUID(as_uuid=True),
            sa.ForeignKey("public.users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("dispatched_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            r"phone ~ '^\+[1-9][0-9]{1,14}$'",
            name="ck_wa_sched_phone_e164",
        ),
        sa.CheckConstraint(
            "status IN ('pending', 'sent', 'cancelled', 'skipped', 'failed')",
            name="ck_wa_sched_status",
        ),
        sa.CheckConstraint(
            "(template_id IS NOT NULL) OR (text_message IS NOT NULL)",
            name="ck_wa_sched_payload_present",
        ),
        schema="public",
    )
    op.create_index(
        "ix_wa_sched_due",
        "whatsapp_scheduled_sends",
        ["scheduled_for"],
        postgresql_where=sa.text("status = 'pending'"),
        schema="public",
    )
    op.create_index(
        "ix_wa_sched_store",
        "whatsapp_scheduled_sends",
        ["store_id", "scheduled_for"],
        schema="public",
    )
    op.create_index(
        "ix_wa_sched_order",
        "whatsapp_scheduled_sends",
        ["related_order_id"],
        postgresql_where=sa.text("related_order_id IS NOT NULL"),
        schema="public",
    )
    _add_rls_for_table("whatsapp_scheduled_sends")

    # ─── 3. whatsapp_dead_letters ────────────────────────────────────
    op.create_table(
        "whatsapp_dead_letters",
        sa.Column(
            "id",
            UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "tenant_id",
            UUID(as_uuid=True),
            sa.ForeignKey("public.tenants.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column(
            "store_id",
            UUID(as_uuid=True),
            sa.ForeignKey("public.stores.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("phone", sa.String(20), nullable=False),
        sa.Column(
            "customer_id",
            UUID(as_uuid=True),
            sa.ForeignKey("public.customers.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "template_id",
            UUID(as_uuid=True),
            sa.ForeignKey("public.whatsapp_templates.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("template_params", JSONB, nullable=True),
        sa.Column("text_message", sa.Text, nullable=True),
        sa.Column("originating_context", sa.String(32), nullable=False),
        sa.Column("originating_context_id", UUID(as_uuid=True), nullable=True),
        sa.Column(
            "error_history",
            JSONB,
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column("error_classification", sa.String(32), nullable=False),
        sa.Column("final_error_code", sa.String(64), nullable=True),
        sa.Column(
            "replay_state",
            sa.String(32),
            nullable=False,
            server_default="not_replayed",
        ),
        sa.Column("replayed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "replayed_by",
            UUID(as_uuid=True),
            sa.ForeignKey("public.users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        # No FK to message_log to keep replay tracking loose-coupled
        sa.Column("replayed_send_id", UUID(as_uuid=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.CheckConstraint(
            r"phone ~ '^\+[1-9][0-9]{1,14}$'",
            name="ck_wa_dl_phone_e164",
        ),
        sa.CheckConstraint(
            "originating_context IN ('order_created', 'order_paid', "
            "'order_status_changed', 'campaign', 'scheduled_send', "
            "'abandoned_cart', 'ad_hoc')",
            name="ck_wa_dl_context",
        ),
        sa.CheckConstraint(
            "error_classification IN ('retriable_exhausted', 'non_retriable')",
            name="ck_wa_dl_classification",
        ),
        sa.CheckConstraint(
            "replay_state IN ('not_replayed', 'replaying', 'replayed_success', "
            "'replayed_failed')",
            name="ck_wa_dl_replay_state",
        ),
        schema="public",
    )
    op.create_index(
        "ix_wa_dl_store_created",
        "whatsapp_dead_letters",
        ["store_id", "created_at"],
        schema="public",
    )
    # Drives the 90-day purge task (FR-035a).
    op.create_index(
        "ix_wa_dl_purge",
        "whatsapp_dead_letters",
        ["created_at"],
        schema="public",
    )
    op.create_index(
        "ix_wa_dl_context",
        "whatsapp_dead_letters",
        ["originating_context", "originating_context_id"],
        schema="public",
    )
    _add_rls_for_table("whatsapp_dead_letters")

    # ─── 4. GIN index on message_log.metadata (research.md R5) ────────
    # Used by the order-event handlers for idempotency lookups keyed on
    # metadata->>'order_id'. IF NOT EXISTS guard handles the case where
    # someone else already added it.
    # NOTE: the original commit used ``message_log`` (singular); the
    # actual table is ``message_logs`` (plural — see
    # infrastructure/database/models/tenant/message_log.py: ``__tablename__
    # = "message_logs"``). Fixed in-flight before any env successfully
    # applied this migration — the CREATE INDEX was the line that
    # blocked CD with ``relation "public.message_log" does not exist``.
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_message_logs_metadata_gin "
        "ON public.message_logs USING gin (metadata)"
    )

    # ─── 5. Seed system templates (FR-030) ───────────────────────────
    # store_id and tenant_id NULL = system-global. The whatsapp_templates
    # FK constraints reference store_id NOT NULL — so we instead use a
    # sentinel: every existing store's tenant gets the system templates
    # injected lazily by the application layer at first-use. For now we
    # seed them under no store; the merchant-hub layer will deal with
    # visibility (Phase 2).
    #
    # Actually re-reading the existing whatsapp_templates schema:
    # store_id is NOT NULL. So system templates can't live as bare rows.
    # Instead we mark is_system=true and seed them per-store at store
    # provisioning time. For this migration we just record the canonical
    # set as a reference rather than insert rows; the application's
    # store-provisioning use-case seeds them.
    #
    # To make Phase 1 functional immediately on existing stores in
    # platform_managed mode, we backfill: for every store where no
    # is_system=true template with this name+language exists yet, insert
    # one. Idempotent via NOT EXISTS subquery.
    # Use sa.text() with `:name` style placeholders instead of
    # exec_driver_sql(`%(name)s`). The latter passes %-placeholders raw to
    # the underlying DBAPI; that works on psycopg2 but asyncpg (which the
    # async stack uses) only understands $1, $2 style and rejects
    # %-syntax with "syntax error at or near %". sa.text() is
    # driver-agnostic — SQLAlchemy rewrites :name to the right form per
    # dialect.
    # ``CAST(:x AS text)`` disambiguates the parameter types for asyncpg.
    # ``:name`` and ``:lang`` appear twice (SELECT + WHERE). Without an
    # explicit cast, asyncpg's protocol-prepare step tries to infer one
    # Postgres type per placeholder and fails:
    #   AmbiguousParameterError: inconsistent types deduced for parameter $1
    #   DETAIL: text versus character varying
    # because the SELECT inserts into a `varchar` column while the WHERE
    # compares against `varchar` too — but the Python str literal flows
    # in as `text`. Forcing the cast gives asyncpg a single resolved type;
    # Postgres implicitly coerces text→varchar on the insert + compare.
    conn = op.get_bind()
    insert_stmt = sa.text(
        """
        INSERT INTO public.whatsapp_templates
          (tenant_id, store_id, name, language, category, status,
           body_text, is_system, created_at, updated_at)
        SELECT s.tenant_id, s.id,
               CAST(:name AS text), CAST(:lang AS text),
               CAST(:cat AS text), 'APPROVED',
               CAST(:body AS text), true, NOW(), NOW()
        FROM public.stores s
        WHERE NOT EXISTS (
            SELECT 1 FROM public.whatsapp_templates t
            WHERE t.store_id = s.id
              AND t.name = CAST(:name AS text)
              AND t.language = CAST(:lang AS text)
        )
        """
    )
    for name, lang, category, body in _SYSTEM_TEMPLATES:
        conn.execute(
            insert_stmt,
            {"name": name, "lang": lang, "cat": category, "body": body},
        )


def downgrade() -> None:
    # System template backfill: best-effort rollback (delete only is_system rows)
    conn = op.get_bind()
    seeded_names = sorted({n for n, _l, _c, _b in _SYSTEM_TEMPLATES})
    for name in seeded_names:
        # Same asyncpg-compat note as upgrade — use sa.text(:name) not %(name)s.
        conn.execute(
            sa.text(
                "DELETE FROM public.whatsapp_templates "
                "WHERE is_system = true AND name = :name"
            ),
            {"name": name},
        )

    op.execute("DROP INDEX IF EXISTS public.ix_message_logs_metadata_gin")

    _drop_rls_for_table("whatsapp_dead_letters")
    op.drop_index(
        "ix_wa_dl_context", table_name="whatsapp_dead_letters", schema="public"
    )
    op.drop_index("ix_wa_dl_purge", table_name="whatsapp_dead_letters", schema="public")
    op.drop_index(
        "ix_wa_dl_store_created", table_name="whatsapp_dead_letters", schema="public"
    )
    op.drop_table("whatsapp_dead_letters", schema="public")

    _drop_rls_for_table("whatsapp_scheduled_sends")
    op.drop_index(
        "ix_wa_sched_order", table_name="whatsapp_scheduled_sends", schema="public"
    )
    op.drop_index(
        "ix_wa_sched_store", table_name="whatsapp_scheduled_sends", schema="public"
    )
    op.drop_index(
        "ix_wa_sched_due", table_name="whatsapp_scheduled_sends", schema="public"
    )
    op.drop_table("whatsapp_scheduled_sends", schema="public")

    _drop_rls_for_table("whatsapp_opt_ins")
    op.drop_index(
        "ix_wa_opt_ins_customer", table_name="whatsapp_opt_ins", schema="public"
    )
    op.drop_index(
        "ix_wa_opt_ins_store_phone_history",
        table_name="whatsapp_opt_ins",
        schema="public",
    )
    op.drop_index(
        "ix_wa_opt_ins_store_phone_active",
        table_name="whatsapp_opt_ins",
        schema="public",
    )
    op.drop_table("whatsapp_opt_ins", schema="public")
