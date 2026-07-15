"""Hourly smart-alerts sweep — the time-critical rule subset (AI-2).

Cheap per store (rollup reads + a few narrow queries + one Redis
snapshot); per-store isolation like the other sweeps. Redis cooldowns
inside the service keep re-fires quiet.
"""

import asyncio
import logging

from src.infrastructure.messaging.celery_app import celery_app

logger = logging.getLogger(__name__)

_task_loop = None


def _run(coro):
    global _task_loop
    if _task_loop is None or _task_loop.is_closed():
        _task_loop = asyncio.new_event_loop()
        asyncio.set_event_loop(_task_loop)
    return _task_loop.run_until_complete(coro)


@celery_app.task(name="tasks.run_alerts_sweep", bind=True, max_retries=1)
def run_alerts_sweep_task(self, store_id: str | None = None):
    """Evaluate smart alerts for all active stores (or one, ad hoc)."""
    try:
        result = _run(_sweep(store_id))
        logger.info(f"alerts_sweep_complete: {result}")
        return result
    except Exception as exc:
        logger.exception("alerts_sweep_failed")
        raise self.retry(exc=exc, countdown=300)


async def _sweep(only_store_id: str | None = None) -> dict:
    from uuid import UUID

    from sqlalchemy import select

    from src.application.services.alert_service import run_store_alerts
    from src.core.utils.store_timezone import resolve_store_timezone_name
    from src.infrastructure.database.connection import AsyncSessionLocal
    from src.infrastructure.database.models.tenant.store import StoreModel

    async with AsyncSessionLocal() as session:
        query = select(
            StoreModel.id,
            StoreModel.tenant_id,
            StoreModel.settings,
            StoreModel.default_currency,
        ).where(StoreModel.status == "ACTIVE")
        if only_store_id:
            query = query.where(StoreModel.id == UUID(only_store_id))
        stores = (await session.execute(query)).all()

    stats = {"stores": len(stores), "alerts_created": 0, "errors": 0}
    for row in stores:
        try:
            async with AsyncSessionLocal() as session:
                async with session.begin():
                    out = await run_store_alerts(
                        session,
                        row.id,
                        row.tenant_id,
                        resolve_store_timezone_name(row.settings),
                        row.default_currency or "EGP",
                    )
            stats["alerts_created"] += out["created"]
        except Exception:
            stats["errors"] += 1
            logger.warning(
                "alerts_store_failed",
                extra={"store_id": str(row.id)},
                exc_info=True,
            )
    return stats
