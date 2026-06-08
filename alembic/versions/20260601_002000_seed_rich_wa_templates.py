"""Seed rich (Bosta-style) WhatsApp system templates.

Inserts the new versioned system templates (order_confirmation_request_v2,
order_confirmation_v3, order_shipped_v3, order_delivered_v2,
payment_received_v2, abandoned_cart_v3) as ``is_system`` rows for every store,
mirroring the per-store backfill in ..._seed_confirm_request_template and
wa_optin_sched_dl_20260524.

Bodies / footers / buttons come from the single source of truth
``src/core/whatsapp_rich_templates.py`` (shared with the platform-WABA
submission script) so the local preview mirror can't drift from what's
submitted to Meta.

IMPORTANT: rows are seeded with ``status='PENDING'`` — NOT 'APPROVED'. The
real Meta approval is asynchronous (submission script → Meta review → status
webhook / poll flips to APPROVED). The send-guard only sends APPROVED
templates, so this avoids attempting a send before Meta has approved. The old
templates keep their APPROVED rows, so sends keep working on the old names
until the new ones are approved (phased cutover).

Revision ID: rich_wa_templates_20260601
Revises: order_wa_confirm_cols_20260601
Create Date: 2026-06-01
"""

import json
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op
from src.core.whatsapp_rich_templates import RICH_TEMPLATES

revision: str = "rich_wa_templates_20260601"
down_revision: str | Sequence[str] | None = "order_wa_confirm_cols_20260601"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Names this migration owns — used by downgrade() to remove exactly these.
_NAMES = sorted({t["name"] for t in RICH_TEMPLATES})


def upgrade() -> None:
    # Per-store backfill, idempotent via NOT EXISTS. asyncpg-compat: explicit
    # CAST(:param AS <type>) on every bind — see the long note in
    # wa_optin_sched_dl_20260524 for why the casts are required. buttons is
    # JSONB so it's cast from a JSON string.
    conn = op.get_bind()
    insert_stmt = sa.text(
        """
        INSERT INTO public.whatsapp_templates
          (tenant_id, store_id, name, language, category, status,
           body_text, footer_text, buttons, is_system,
           submitted_at, created_at, updated_at)
        SELECT s.tenant_id, s.id,
               CAST(:name AS text), CAST(:lang AS text),
               CAST(:cat AS text), 'PENDING',
               CAST(:body AS text), CAST(:footer AS text),
               CAST(:buttons AS jsonb), true,
               -- submitted_at must be set so the PENDING-template poller
               -- (filters on submitted_at <= now-5m) actually considers these
               -- system rows and syncs their Meta status. Without it the rows
               -- stay PENDING in the hub forever.
               NOW(), NOW(), NOW()
        FROM public.stores s
        WHERE NOT EXISTS (
            SELECT 1 FROM public.whatsapp_templates t
            WHERE t.store_id = s.id
              AND t.name = CAST(:name AS text)
              AND t.language = CAST(:lang AS text)
        )
        """
    )
    for t in RICH_TEMPLATES:
        conn.execute(
            insert_stmt,
            {
                "name": t["name"],
                "lang": t["language"],
                "cat": t["category"],
                "body": t["body"],
                "footer": t.get("footer"),
                "buttons": json.dumps(t.get("buttons") or []),
            },
        )


def downgrade() -> None:
    conn = op.get_bind()
    conn.execute(
        sa.text(
            "DELETE FROM public.whatsapp_templates "
            "WHERE is_system = true AND name = ANY(:names)"
        ),
        {"names": _NAMES},
    )
