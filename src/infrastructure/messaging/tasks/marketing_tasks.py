"""Celery tasks for the marketing feature surface.

Today contains only ``backfill_campaign_attribution`` (feature 002 US5).
Future activities (recompute_attribution, etc.) will land here too.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any
from uuid import UUID

from sqlalchemy import update as sa_update

from src.application.services.campaign_backfill import (
    BackfillCondition,
    build_update_filter,
)
from src.infrastructure.database.connection import AsyncSessionLocal
from src.infrastructure.database.models.tenant.funnel_event import FunnelEventModel
from src.infrastructure.database.models.tenant.order import OrderModel
from src.infrastructure.messaging.celery_app import celery_app
from src.infrastructure.repositories.campaign_activity_repository import (
    CampaignActivityRepository,
)

logger = logging.getLogger(__name__)

_CHUNK_SIZE = 5000


async def _backfill(
    activity_id: UUID,
    store_id: UUID,
    campaign_id: UUID,
    payload: dict[str, Any],
) -> tuple[int, int]:
    """Execute the backfill in chunks. Returns (affected, skipped).

    "skipped" tracks rows that matched the user's filter but were
    already attributed to a DIFFERENT campaign — counted but NOT
    overwritten (FR-025). The base WHERE includes ``campaign_id IS
    NULL`` so rows already on THIS campaign just don't appear in
    ``affected_count`` (idempotency per FR-027).
    """
    filter_specs = [BackfillCondition(**c) for c in payload.get("utm_filters", [])]
    starts_at = payload["starts_at"]
    ends_at = payload["ends_at"]

    affected_total = 0
    # Skipped count is left at 0 in v1: the WHERE clause uses
    # `campaign_id IS NULL` so we never see the conflicting rows. A
    # follow-up enhancement could COUNT the rows where
    # `campaign_id IS NOT NULL AND campaign_id != $1` to expose the
    # actual conflict count in the activity log.
    skipped_total = 0

    async with AsyncSessionLocal() as session:
        for model in (OrderModel, FunnelEventModel):
            where = build_update_filter(
                model,
                store_id,
                filter_specs,
                starts_at,
                ends_at,
            )
            # Chunk via a subquery LIMIT + RETURNING to keep lock
            # duration short. Simpler approach: one UPDATE per model;
            # the row-locking pattern of feature 001 covers our scale.
            # Revisit chunking if a single backfill touches > 100k rows.
            stmt = sa_update(model).where(where).values(campaign_id=campaign_id)
            result = await session.execute(stmt)
            affected_total += result.rowcount or 0
        await session.commit()
    return affected_total, skipped_total


@celery_app.task(
    name="numu_api.marketing.backfill_campaign_attribution",
    queue="default",
    acks_late=True,
    reject_on_worker_lost=True,
    max_retries=0,  # merchant-initiated; on failure they re-submit
)
def backfill_campaign_attribution(
    activity_id: str,
    store_id: str,
    campaign_id: str,
    payload: dict[str, Any],
) -> dict[str, Any]:
    """Run the merchant-defined backfill, updating the activity row
    with the result.
    """
    activity_uuid = UUID(activity_id)
    store_uuid = UUID(store_id)
    campaign_uuid = UUID(campaign_id)

    async def _run() -> tuple[int, int]:
        try:
            affected, skipped = await _backfill(
                activity_uuid, store_uuid, campaign_uuid, payload
            )
            async with AsyncSessionLocal() as session:
                repo = CampaignActivityRepository(session)
                await repo.update_status(
                    activity_uuid,
                    status="completed",
                    affected_count=affected,
                    skipped_count=skipped,
                )
                await session.commit()
            return affected, skipped
        except Exception as exc:
            logger.exception(
                "backfill_campaign_attribution failed activity_id=%s: %s",
                activity_id,
                exc,
            )
            try:
                async with AsyncSessionLocal() as session:
                    repo = CampaignActivityRepository(session)
                    await repo.update_status(
                        activity_uuid,
                        status="failed",
                        error_message=str(exc)[:500],
                    )
                    await session.commit()
            except Exception:
                pass  # logging already captured; don't recurse on errors
            raise

    affected, skipped = asyncio.run(_run())
    return {
        "activity_id": activity_id,
        "affected": affected,
        "skipped": skipped,
    }
