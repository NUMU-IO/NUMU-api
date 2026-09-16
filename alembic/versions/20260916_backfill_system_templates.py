"""Backfill the WhatsApp system template rows for stores that have none.

Revision ID: wa_tmpl_backfill_20260916
Revises: product_requests_20260916
Create Date: 2026-09-16

Every automated WhatsApp send is guarded on a per-store row in
``whatsapp_templates`` being APPROVED. Those rows were only ever created by
one-shot backfills inside migrations, so every store created since the last
one (rich_wa_templates_20260601 / cod_ap_seed_20260718) has none — the guard
reads no row, and order confirmation, shipped, delivered and all of COD
Autopilot skip silently. Measured on prod: a May store has 32 rows with the
Autopilot templates APPROVED; a September store has zero.

`create_store` now seeds these rows for new stores. This catches the ones
already created. Rows land PENDING and
``numu_api.whatsapp.poll_pending_templates`` flips them to APPROVED off the
platform WABA within 15 minutes.
"""

import json
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op
from src.core.whatsapp_rich_templates import RICH_TEMPLATES

revision: str = "wa_tmpl_backfill_20260916"
down_revision: str | Sequence[str] | None = "product_requests_20260916"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


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
    for tmpl in RICH_TEMPLATES:
        conn.execute(
            insert_stmt,
            {
                "name": tmpl["name"],
                "lang": tmpl["language"],
                "cat": tmpl.get("category", "UTILITY"),
                "body": tmpl["body"],
                "footer": tmpl.get("footer"),
                "buttons": json.dumps(tmpl.get("buttons"))
                if tmpl.get("buttons")
                else None,
            },
        )


def downgrade() -> None:
    """No-op.

    The rows are indistinguishable from the ones the earlier seeds created,
    and deleting by name would take those with them — re-darkening the stores
    that have been sending happily for months.
    """
