"""Celery tasks for the offers-v2 / Promotions feature.

Three jobs:

* ``expire_promotions`` — cheap, runs every 5 minutes. Flips status
  ``active``↔``expired``↔``scheduled`` based on the configured window so
  the storefront stops serving past-window promos without waiting for the
  hourly cache TTL.

* ``prune_promotion_events`` — daily, deletes raw rows older than 90
  days. Aggregated rollups (step 13) preserve the historical numbers.

* ``rollup_promotion_events_daily`` — daily, fills tomorrow's
  ``promotion_event_daily`` aggregate. The aggregate table itself ships
  in step 13 — until then this task is a no-op stub so the registration
  point exists for ops.

All three respect the existing default Celery queue.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime, timedelta

from sqlalchemy import delete, update

from src.infrastructure.messaging.celery_app import celery_app

logger = logging.getLogger(__name__)


# --------------------------------------------------------------------------- #
# Expiry job                                                                  #
# --------------------------------------------------------------------------- #


async def _expire_async() -> dict[str, int]:
    """Move promos in/out of ACTIVE based on the schedule window.

    Promotion lifecycle states are *advisory* — the storefront re-checks
    `starts_at` / `ends_at` at read time too — but flipping the row's
    status keeps merchant lists honest and lets the cache invalidator
    publish a `PromotionUpdated` event.
    """
    from src.infrastructure.database.connection import AsyncSessionLocal
    from src.infrastructure.database.models.tenant.promotion import PromotionModel

    now = datetime.now(UTC)
    counts = {"expired": 0, "activated": 0}

    async with AsyncSessionLocal() as session:
        # Active → expired
        expired_stmt = (
            update(PromotionModel)
            .where(
                PromotionModel.status == "active",
                PromotionModel.ends_at.is_not(None),
                PromotionModel.ends_at < now,
            )
            .values(status="expired")
        )
        result = await session.execute(expired_stmt)
        counts["expired"] = result.rowcount or 0

        # Scheduled → active (window has begun)
        activated_stmt = (
            update(PromotionModel)
            .where(
                PromotionModel.status == "scheduled",
                PromotionModel.starts_at.is_not(None),
                PromotionModel.starts_at <= now,
            )
            .values(status="active")
        )
        result = await session.execute(activated_stmt)
        counts["activated"] = result.rowcount or 0

        await session.commit()
    return counts


@celery_app.task(
    name="tasks.expire_promotions",
    bind=True,
    max_retries=1,
    default_retry_delay=120,
    acks_late=True,
)
def expire_promotions(self) -> dict:
    """Sweep all promotions and flip status against the schedule window."""
    try:
        counts = asyncio.run(_expire_async())
        logger.info("Promotion expiry sweep: %s", counts)
        return {"status": "ok", **counts}
    except Exception as exc:
        logger.exception("Promotion expiry sweep failed, retrying…")
        raise self.retry(exc=exc)


# --------------------------------------------------------------------------- #
# Event pruning                                                               #
# --------------------------------------------------------------------------- #


async def _prune_async(retention_days: int) -> int:
    from src.infrastructure.database.connection import AsyncSessionLocal
    from src.infrastructure.database.models.tenant.promotion import (
        PromotionEventModel,
    )

    cutoff = datetime.now(UTC) - timedelta(days=retention_days)
    async with AsyncSessionLocal() as session:
        stmt = delete(PromotionEventModel).where(
            PromotionEventModel.occurred_at < cutoff
        )
        result = await session.execute(stmt)
        await session.commit()
        return int(result.rowcount or 0)


@celery_app.task(
    name="tasks.prune_promotion_events",
    bind=True,
    max_retries=2,
    default_retry_delay=600,
    acks_late=True,
)
def prune_promotion_events(self, retention_days: int = 90) -> dict:
    """Delete raw `promotion_events` older than the retention window."""
    try:
        deleted = asyncio.run(_prune_async(retention_days))
        logger.info("Promotion-event prune: deleted %d rows", deleted)
        return {"status": "ok", "deleted": deleted, "retention_days": retention_days}
    except Exception as exc:
        logger.exception("Promotion-event prune failed, retrying…")
        raise self.retry(exc=exc)


# --------------------------------------------------------------------------- #
# Daily rollup — stub                                                         #
# --------------------------------------------------------------------------- #


@celery_app.task(
    name="tasks.rollup_promotion_events_daily",
    bind=True,
    max_retries=1,
    default_retry_delay=900,
    acks_late=True,
)
def rollup_promotion_events_daily(self, day: str | None = None) -> dict:
    """Daily aggregate into `promotion_event_daily`.

    The aggregate table lands in step 13 of the offers-v2 plan; this
    function exists now so Beat can schedule it without churn later.
    """
    logger.info(
        "rollup_promotion_events_daily called (target_day=%s) — stub, no-op until step 13",
        day,
    )
    return {"status": "skipped", "reason": "rollup table not yet created"}
