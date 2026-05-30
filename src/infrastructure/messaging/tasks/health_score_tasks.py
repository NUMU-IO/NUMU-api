"""Celery task for daily merchant health score calculation.

Runs once per day, computes health scores for all active stores,
and persists results in store.settings["health_score"].
"""

import asyncio
import logging

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
    name="tasks.calculate_health_scores",
    bind=True,
    max_retries=2,
    default_retry_delay=600,
)
def calculate_health_scores_task(self):
    """Calculate health scores for all active stores."""
    try:
        result = run_async(_calculate_all_scores())
        logger.info(f"Health score calculation complete: {result}")
        return result
    except Exception as exc:
        logger.exception("Health score calculation failed")
        raise self.retry(exc=exc)


async def _calculate_all_scores() -> dict:
    """Calculate and persist health scores for all active stores."""
    from sqlalchemy import select, update

    from src.application.services.health_score_service import (
        HEALTH_SCORE_WINDOW_DAYS,
        calculate_store_health_score,
    )
    from src.infrastructure.database.connection import AsyncSessionLocal
    from src.infrastructure.database.models.tenant.store import StoreModel

    stats = {"processed": 0, "updated": 0, "errors": 0}

    async with AsyncSessionLocal() as session:
        # Get all active stores
        result = await session.execute(
            select(StoreModel.id, StoreModel.settings).where(
                StoreModel.status == "ACTIVE"
            )
        )
        stores = result.all()

        for store_row in stores:
            store_id = store_row.id
            current_settings = dict(store_row.settings) if store_row.settings else {}
            stats["processed"] += 1

            try:
                score_data = await calculate_store_health_score(
                    session=session,
                    store_id=store_id,
                    days=HEALTH_SCORE_WINDOW_DAYS,
                )

                # Persist in store.settings["health_score"]
                current_settings["health_score"] = score_data

                await session.execute(
                    update(StoreModel)
                    .where(StoreModel.id == store_id)
                    .values(settings=current_settings)
                )
                stats["updated"] += 1

            except Exception as e:
                logger.warning(f"Health score failed for store {store_id}: {e}")
                stats["errors"] += 1

        await session.commit()

    return stats
