"""Seed COD Autopilot WhatsApp templates (ship digest + delivery check).

Feature 004-cod-autopilot. Inserts ``cod_ship_digest_v1`` and
``order_delivery_check_v1`` (en_US + ar) as per-store ``is_system`` rows,
mirroring rich_wa_templates_20260601. Bodies / footers / buttons come from
the single source of truth ``src/core/whatsapp_rich_templates.py`` (shared
with the platform-WABA submission script) so the local mirror can't drift.

Rows are seeded ``status='PENDING'`` — the send guard blocks unapproved
templates, so Autopilot sends stay dark until Meta approval flips the rows
to APPROVED (which is also the feature's rollout switch).

Revision ID: cod_ap_seed_20260718
Revises: cod_autopilot_20260718
Create Date: 2026-07-18
"""

import json
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op
from src.core.whatsapp_rich_templates import RICH_TEMPLATES

revision: str = "cod_ap_seed_20260718"
down_revision: str | Sequence[str] | None = "cod_autopilot_20260718"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Only the two Autopilot templates — earlier registry entries were seeded
# by rich_wa_templates_20260601 and must not be re-owned here.
_NAMES = ["cod_ship_digest_v1", "order_delivery_check_v1"]
_TEMPLATES = [t for t in RICH_TEMPLATES if t["name"] in _NAMES]


def upgrade() -> None:
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
    for t in _TEMPLATES:
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
