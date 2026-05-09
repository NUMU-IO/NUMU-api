"""Celery tasks for the offers-v2 / Promotions feature.

Three jobs:

* ``expire_promotions`` — cheap, runs every 5 minutes. Flips status
  ``active``↔``expired``↔``scheduled`` based on the configured window so
  the storefront stops serving past-window promos without waiting for the
  hourly cache TTL.

* ``prune_promotion_events`` — daily, deletes raw rows older than 90
  days. Aggregated rollups preserve the historical numbers.

* ``rollup_promotion_events_daily`` — daily, fills yesterday's row in
  ``promotion_event_daily`` so the merchant analytics endpoint can read
  in O(days) instead of scanning the append-only event log. Idempotent
  via ``ON CONFLICT DO UPDATE`` so retries / backfills are safe.

All three respect the existing default Celery queue.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime, timedelta

from sqlalchemy import delete, func, update

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
# Daily rollup                                                                #
# --------------------------------------------------------------------------- #


async def _rollup_async(target_day: datetime) -> dict[str, int]:
    """Aggregate `promotion_events` for `target_day` into the daily table.

    Idempotent via `INSERT ... ON CONFLICT (promotion_id, day, event_type)
    DO UPDATE`. Re-running for the same day collapses cleanly — useful
    on retries and for backfilling historical days. Counters reflect the
    full row set in `promotion_events` for the day, not deltas, so a
    second run after late-arriving events still produces correct totals.
    """
    from sqlalchemy import select, text
    from sqlalchemy.dialects.postgresql import insert as pg_insert

    from src.infrastructure.database.connection import AsyncSessionLocal
    from src.infrastructure.database.models.tenant.promotion import (
        PromotionEventDailyModel,
        PromotionEventModel,
    )

    # `target_day` is interpreted in UTC. Window is [start, start + 1 day).
    start = target_day.replace(hour=0, minute=0, second=0, microsecond=0)
    end = start + timedelta(days=1)
    day_only = start.date()

    async with AsyncSessionLocal() as session:
        # Bypass RLS — this task aggregates across every tenant.
        await session.execute(text("SELECT set_config('app.rls_bypass','true',true)"))

        # One row per (tenant, store, promotion, event_type) for the day.
        agg_stmt = (
            select(
                PromotionEventModel.tenant_id,
                PromotionEventModel.store_id,
                PromotionEventModel.promotion_id,
                PromotionEventModel.event_type,
                func.count().label("count"),
                func.count(func.distinct(PromotionEventModel.session_id)).label(
                    "unique_visitors"
                ),
                func.coalesce(
                    func.sum(PromotionEventModel.discount_amount_cents), 0
                ).label("discount_total_cents"),
            )
            .where(
                PromotionEventModel.occurred_at >= start,
                PromotionEventModel.occurred_at < end,
            )
            .group_by(
                PromotionEventModel.tenant_id,
                PromotionEventModel.store_id,
                PromotionEventModel.promotion_id,
                PromotionEventModel.event_type,
            )
        )
        rows = (await session.execute(agg_stmt)).all()
        if not rows:
            return {"rows_aggregated": 0, "rows_upserted": 0}

        upsert_count = 0
        for row in rows:
            stmt = pg_insert(PromotionEventDailyModel).values(
                tenant_id=row.tenant_id,
                store_id=row.store_id,
                promotion_id=row.promotion_id,
                day=day_only,
                event_type=row.event_type,
                count=int(row.count or 0),
                unique_visitors=int(row.unique_visitors or 0),
                discount_total_cents=int(row.discount_total_cents or 0),
                # `revenue_cents` is left at the default 0 for now — the
                # order-side attribution rollup (spec §4) lands once the
                # convert-event writer ties order_id → promotion(s).
                revenue_cents=0,
            )
            stmt = stmt.on_conflict_do_update(
                index_elements=["promotion_id", "day", "event_type"],
                set_={
                    "tenant_id": stmt.excluded.tenant_id,
                    "store_id": stmt.excluded.store_id,
                    "count": stmt.excluded.count,
                    "unique_visitors": stmt.excluded.unique_visitors,
                    "discount_total_cents": stmt.excluded.discount_total_cents,
                    "revenue_cents": stmt.excluded.revenue_cents,
                    "rolled_up_at": func.now(),
                },
            )
            await session.execute(stmt)
            upsert_count += 1
        await session.commit()
        return {"rows_aggregated": len(rows), "rows_upserted": upsert_count}


@celery_app.task(
    name="tasks.rollup_promotion_events_daily",
    bind=True,
    max_retries=1,
    default_retry_delay=900,
    acks_late=True,
)
def rollup_promotion_events_daily(self, day: str | None = None) -> dict:
    """Roll up `promotion_events` into the daily aggregate table.

    `day` argument: ISO date string `YYYY-MM-DD` (UTC). Defaults to
    yesterday so the nightly Beat run picks the most recently complete
    day. Pass an older value to backfill.
    """
    if day is None:
        target = (datetime.now(UTC) - timedelta(days=1)).replace(
            hour=0, minute=0, second=0, microsecond=0
        )
    else:
        try:
            target = datetime.fromisoformat(day).replace(tzinfo=UTC)
        except ValueError as exc:
            logger.warning("rollup_promotion_events_daily bad day=%r: %s", day, exc)
            return {"status": "error", "reason": "invalid day"}

    try:
        result = asyncio.run(_rollup_async(target))
        logger.info(
            "rollup_promotion_events_daily target=%s result=%s",
            target.date(),
            result,
        )
        return {"status": "ok", "day": target.date().isoformat(), **result}
    except Exception as exc:  # noqa: BLE001
        logger.exception("rollup_promotion_events_daily failed for %s", target.date())
        raise self.retry(exc=exc)
