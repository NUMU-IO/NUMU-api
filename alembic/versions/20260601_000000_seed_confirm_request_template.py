"""Seed order_confirmation_request_v1 system template (COD confirm flow)

Adds the active "tap to confirm" COD template (en_US + ar) as is_system
rows, one per store, mirroring the per-store backfill used for the
Phase-1 system templates in wa_optin_sched_dl_20260524. The send-guard
reads the seeded row's `status` to allow sends; real approval status keeps
syncing via the message_template_status_update webhook.

Unlike the URL-CTA templates, this one carries a QUICK_REPLY "Confirm"
button. The button structure is supplied to Meta from EGYPTIAN_TEMPLATES
at send-time, so only body_text is seeded here (matching the existing
system-template seed convention).

Revision ID: confirm_req_tmpl_20260601
Revises: merge_v3_wa_heads_20260730
Create Date: 2026-06-01
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "confirm_req_tmpl_20260601"
down_revision: str | Sequence[str] | None = "merge_v3_wa_heads_20260730"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


# (name, language, category, body_text) — same shape as _SYSTEM_TEMPLATES.
# Body satisfies Meta's validator: does not start or end on a variable.
_TEMPLATES: list[tuple[str, str, str, str]] = [
    (
        "order_confirmation_request_v1",
        "en_US",
        "UTILITY",
        "Hi {{1}}, your order {{2}} totals {{3}}, delivering to {{4}}. Tap "
        "Confirm so we can start preparing it.",
    ),
    (
        "order_confirmation_request_v1",
        "ar",
        "UTILITY",
        "أهلاً يا {{1}}، طلبك رقم {{2}} إجماليه {{3}} وهيتسلّم على {{4}}. اضغط "
        "تأكيد عشان نبدأ التحضير.",
    ),
]


def upgrade() -> None:
    # Per-store backfill, idempotent via NOT EXISTS. asyncpg-compat: use
    # sa.text(:name) with explicit CAST(... AS text) — see the long note in
    # wa_optin_sched_dl_20260524 for why the casts are required.
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
    for name, lang, category, body in _TEMPLATES:
        conn.execute(
            insert_stmt,
            {"name": name, "lang": lang, "cat": category, "body": body},
        )


def downgrade() -> None:
    conn = op.get_bind()
    conn.execute(
        sa.text(
            "DELETE FROM public.whatsapp_templates "
            "WHERE is_system = true AND name = :name"
        ),
        {"name": "order_confirmation_request_v1"},
    )
