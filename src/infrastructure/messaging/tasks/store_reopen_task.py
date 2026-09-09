"""Reopen stores whose scheduled closure has elapsed.

`set_store_availability` can close a storefront with a `reopen_at`. Something
has to actually reopen it, or "close until Saturday" is just "close" with a
promise nobody keeps — the worst version of this feature, because the merchant
stops checking.

Runs every 15 minutes rather than nightly: a merchant who says "closed until
2pm" means 2pm, and a store staying shut until 03:00 the next morning is lost
orders. Fifteen minutes is the coarsest granularity that still reads as
"reopened when I said".

Idempotent: it only touches stores that are INACTIVE *and* carry an elapsed
`reopen_at`, and it clears the marker as it goes, so a re-run does nothing.
A store a merchant reopened by hand already had its marker cleared by the
applier; a store an admin suspended is SUSPENDED, not INACTIVE, and is never
touched here.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime

from sqlalchemy import select

from src.infrastructure.messaging.celery_app import celery_app

logger = logging.getLogger(__name__)

_task_loop = None


def _run(coro):
    global _task_loop
    if _task_loop is None or _task_loop.is_closed():
        _task_loop = asyncio.new_event_loop()
        asyncio.set_event_loop(_task_loop)
    return _task_loop.run_until_complete(coro)


@celery_app.task(name="tasks.reopen_scheduled_stores", bind=True, max_retries=1)
def reopen_scheduled_stores_task(self):
    """Flip back any store whose reopen_at has passed."""
    try:
        return _run(_reopen())
    except Exception as exc:
        logger.exception("store_reopen_failed")
        raise self.retry(exc=exc, countdown=300)


async def _reopen() -> dict:
    from src.core.entities.store import StoreStatus
    from src.infrastructure.database.connection import AsyncSessionLocal
    from src.infrastructure.database.models import StoreModel

    now = datetime.now(UTC)
    reopened: list[str] = []

    async with AsyncSessionLocal() as session:
        rows = await session.execute(
            select(StoreModel).where(
                StoreModel.status == StoreStatus.INACTIVE,
                StoreModel.settings["reopen_at"].astext.isnot(None),
            )
        )
        for store in rows.scalars().all():
            raw = (store.settings or {}).get("reopen_at")
            try:
                due = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
            except (TypeError, ValueError):
                # An unparseable marker would otherwise keep the store shut
                # forever; drop it and leave the store for a human.
                logger.warning("store_reopen_bad_marker store_id=%s", store.id)
                settings = dict(store.settings or {})
                settings.pop("reopen_at", None)
                store.settings = settings
                continue
            if due.tzinfo is None:
                due = due.replace(tzinfo=UTC)
            if due > now:
                continue

            settings = dict(store.settings or {})
            settings.pop("reopen_at", None)
            store.settings = settings
            store.status = StoreStatus.ACTIVE
            reopened.append(str(store.id))

        await session.commit()

    if reopened:
        logger.info("stores_reopened count=%d ids=%s", len(reopened), reopened)
    return {"reopened": len(reopened), "store_ids": reopened}
