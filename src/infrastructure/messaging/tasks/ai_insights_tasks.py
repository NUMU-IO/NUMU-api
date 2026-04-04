"""Celery task for daily AI insights generation.

Runs at 5 AM UTC (after rollup at 3:30 + health score at 4:00).
Generates insights for all active stores and caches in store.settings.
"""

import asyncio
import logging
from datetime import date, timedelta

from src.infrastructure.messaging.celery_app import celery_app

logger = logging.getLogger(__name__)

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
    from src.application.services.ai_insights_service import generate_insights
    from src.infrastructure.database.connection import async_session_factory
    from src.infrastructure.repositories.analytics_rollup_repository import (
        AnalyticsRollupRepository,
    )
    from src.infrastructure.repositories.store_repository import StoreRepository

    async with async_session_factory() as session:
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

                # Cache in store.settings
                current_settings = dict(store.settings) if store.settings else {}
                current_settings["ai_insights"] = result_ar
                current_settings["ai_insights_en"] = result_en
                store.settings = current_settings
                await store_repo.update(store)

                generated += 1
            except Exception as e:
                logger.warning(
                    "store_insights_failed",
                    store_id=str(store.id),
                    error=str(e),
                )
                failed += 1

        await session.commit()

        logger.info(
            "ai_insights_completed",
            generated=generated,
            skipped=skipped,
            failed=failed,
            total=len(stores),
        )
