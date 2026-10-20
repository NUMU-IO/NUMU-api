"""Reviews app: give every existing store the app's WhatsApp template rows.

Revision ID: rev_templates_20261020
Revises: trk_templates_20261018

``app_reviews_request_v1`` (ar, en) joined ``RICH_TEMPLATES``, so
``seed_system_templates`` gives new stores the rows; this gives them to the
stores that already exist, the same way ``trk_templates_20261018`` did. Rows
land PENDING; ``poll_pending_templates`` flips them to APPROVED once Meta
approves the template on the platform account. Nothing sends it until a
store installs the app.
"""

import json
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op
from src.core.whatsapp_rich_templates import RICH_TEMPLATES

revision: str = "rev_templates_20261020"
down_revision: str | Sequence[str] | None = "trk_templates_20261018"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

NAME = "app_reviews_request_v1"


def upgrade() -> None:
    conn = op.get_bind()
    for tmpl in (t for t in RICH_TEMPLATES if t["name"] == NAME):
        conn.execute(
            sa.text(
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
            ),
            {
                "name": tmpl["name"],
                "lang": tmpl["language"],
                "cat": tmpl["category"],
                "body": tmpl["body"],
                "footer": tmpl.get("footer"),
                "buttons": json.dumps(tmpl["buttons"]),
            },
        )


def downgrade() -> None:
    # A new name, so deleting by it touches nothing else.
    op.execute(
        sa.text("DELETE FROM public.whatsapp_templates WHERE name = :name").bindparams(
            name=NAME
        )
    )
