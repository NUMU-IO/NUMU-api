"""Celery task for daily AI insights generation.

Runs at 5 AM UTC (after rollup at 3:30 + health score at 4:00).
Generates insights for all active stores and caches in store.settings.
"""

import asyncio
from datetime import date, timedelta

from src.core.logging import get_logger
from src.infrastructure.messaging.celery_app import celery_app

logger = get_logger(__name__)

_task_loop = None


def run_async(coro):
    global _task_loop
    if _task_loop is None or _task_loop.is_closed():
        _task_loop = asyncio.new_event_loop()
        asyncio.set_event_loop(_task_loop)
    return _task_loop.run_until_complete(coro)


@celery_app.task(
    name="tasks.generate_ai_insights",
    bind=True,
    max_retries=2,
    default_retry_delay=300,
    acks_late=True,
)
def generate_ai_insights_task(self):
    """Generate AI insights for all active stores."""
    try:
        run_async(_generate_insights_for_all_stores())
    except Exception as exc:
        logger.error("ai_insights_task_failed", error=str(exc))
        raise self.retry(exc=exc)


async def _generate_insights_for_all_stores():
    from sqlalchemy import func, literal, update
    from sqlalchemy.dialects.postgresql import JSONB

    from src.application.services.ai_insights_service import generate_insights
    from src.infrastructure.database.connection import AsyncSessionLocal
    from src.infrastructure.database.models.tenant.store import StoreModel
    from src.infrastructure.repositories.analytics_rollup_repository import (
        AnalyticsRollupRepository,
    )
    from src.infrastructure.repositories.store_repository import StoreRepository

    async with AsyncSessionLocal() as session:
        store_repo = StoreRepository(session)
        rollup_repo = AnalyticsRollupRepository(session)

        # Get all active stores
        stores = await store_repo.get_all(limit=10000)
        today = date.today()
        date_from = today - timedelta(days=35)

        generated = 0
        skipped = 0
        failed = 0

        for store in stores:
            try:
                rollups = await rollup_repo.get_range(store.id, date_from, today)

                if len(rollups) < 3:
                    skipped += 1
                    continue

                currency = (
                    store.default_currency.value if store.default_currency else "EGP"
                )

                # Generate insights in both languages
                result_ar = await generate_insights(
                    rollups, store_currency=currency, lang="ar"
                )
                result_en = await generate_insights(
                    rollups, store_currency=currency, lang="en"
                )

                # Cache in store.settings: merge only these two keys, in SQL,
                # and commit per store. Writing back the store read at the start
                # of the run would revert any store edit saved while the run is
                # on, and one transaction for the whole run would keep every
                # updated store row locked until the end.
                patch = {"ai_insights": result_ar, "ai_insights_en": result_en}
                await session.execute(
                    update(StoreModel)
                    .where(StoreModel.id == store.id)
                    .values(
                        settings=func.coalesce(
                            StoreModel.settings, literal({}, type_=JSONB)
                        ).op("||")(literal(patch, type_=JSONB))
                    )
                )
                await session.commit()

                generated += 1
            except Exception as e:
                # A failed statement aborts the transaction; without this every
                # later store would fail too.
                await session.rollback()
                logger.warning(
                    "store_insights_failed",
                    store_id=str(store.id),
                    error=str(e),
                )
                failed += 1

        logger.info(
            "ai_insights_completed",
            generated=generated,
            skipped=skipped,
            failed=failed,
            total=len(stores),
        )
