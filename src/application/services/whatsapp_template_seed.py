"""Give every store the system WhatsApp template rows it needs to send.

Every automated WhatsApp send is guarded on a per-store row in
``whatsapp_templates`` being APPROVED (FR-029). Platform-managed stores all
send through NUMU's single WABA, so those rows are a local mirror of one
shared set of Meta templates — but the mirror was only ever populated by
backfill migrations (``INSERT … FROM public.stores``), which run once.

A store created after the last such migration therefore has NO rows, the
guard reads ``template_status = None``, and every automation — order
confirmation, shipped, delivered, and all of COD Autopilot — skips silently.
Nothing errors; nothing sends. Measured on prod: a store created in May has
32 rows with the Autopilot templates APPROVED; one created in September has
zero.

Rows are seeded PENDING. ``numu_api.whatsapp.poll_pending_templates`` runs
every 15 minutes, reads the platform WABA, and flips them to APPROVED — the
platform templates are already approved, so a new store becomes live within
one poll without any human step.
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.logging import get_logger
from src.core.whatsapp_rich_templates import RICH_TEMPLATES
from src.infrastructure.database.models.tenant.whatsapp_template import (
    WhatsAppTemplateModel,
)

logger = get_logger(__name__)


async def seed_system_templates(
    session: AsyncSession, *, store_id: UUID, tenant_id: UUID
) -> int:
    """Insert the system template rows this store is missing. Idempotent.

    Returns how many rows were added. Existing rows are never touched: their
    status is owned by the webhook and the poll task.
    """
    existing = set(
        (
            await session.execute(
                select(
                    WhatsAppTemplateModel.name,
                    WhatsAppTemplateModel.language,
                ).where(WhatsAppTemplateModel.store_id == store_id)
            )
        ).all()
    )

    now = datetime.now(UTC)
    added = 0
    for tmpl in RICH_TEMPLATES:
        key = (tmpl["name"], tmpl["language"])
        if key in existing:
            continue
        session.add(
            WhatsAppTemplateModel(
                tenant_id=tenant_id,
                store_id=store_id,
                name=tmpl["name"],
                language=tmpl["language"],
                category=tmpl.get("category", "UTILITY"),
                status="PENDING",
                body_text=tmpl["body"],
                footer_text=tmpl.get("footer"),
                buttons=tmpl.get("buttons"),
                is_system=True,
                submitted_at=now,
            )
        )
        added += 1

    if added:
        await session.flush()
        logger.info(
            "whatsapp_system_templates_seeded",
            store_id=str(store_id),
            added=added,
        )
    return added
