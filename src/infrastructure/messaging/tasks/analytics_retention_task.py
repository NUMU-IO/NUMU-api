"""Celery task — purge analytics events older than the retention window.

The funnel/page-view tables grow ~linearly with traffic and have no
pruning today, so a busy store accumulates millions of rows the
analytics endpoints have to scan past. This task deletes events older
than ``ANALYTICS_RETENTION_DAYS`` (default 180) on a daily Celery Beat
schedule.

Two design notes:

* We drop full days, not individual rows: ``WHERE created_at < cutoff``
  with the ``ix_*_store_session`` / ``ix_orders_store_created_status``
  partial indexes makes this cheap even on multi-million-row tables.
* The task fails open — analytics is non-critical; if Postgres is
  busy or the cutoff math is wrong, the next day's run picks up where
  this one left off. We log the row counts for the retention audit.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime, timedelta

from sqlalchemy import delete

from src.infrastructure.messaging.celery_app import celery_app

logger = logging.getLogger(__name__)


def _run_purge(retention_days: int) -> dict[str, int]:
    return asyncio.run(_async_purge(retention_days))


async def _async_purge(retention_days: int) -> dict[str, int]:
    from src.infrastructure.database.connection import (
        AsyncSessionLocal as async_session_factory,
    )
    from src.infrastructure.database.models.tenant.funnel_event import (
        FunnelEventModel,
    )
    from src.infrastructure.database.models.tenant.page_view import PageViewModel

    cutoff = datetime.now(UTC) - timedelta(days=retention_days)
    results: dict[str, int] = {}

    async with async_session_factory() as session:
        for label, model in [
            ("funnel_events", FunnelEventModel),
            ("page_views", PageViewModel),
        ]:
            stmt = delete(model).where(model.created_at < cutoff)  # type: ignore[attr-defined]
            result = await session.execute(stmt)
            results[label] = result.rowcount  # type: ignore[assignment]
        await session.commit()

    return results


@celery_app.task(
    name="tasks.purge_analytics_events",
    bind=True,
    max_retries=2,
    default_retry_delay=600,
    acks_late=True,
)
def purge_analytics_events(self, retention_days: int | None = None) -> dict:
    """Delete funnel_events + page_views older than *retention_days*."""
    if retention_days is None:
        # 180 days is the default — generous enough for year-over-year
        # comparison won't fire from raw events, but tight enough to
        # bound table growth. Override per-deployment via the env var.
        try:
            from src.config import settings

            retention_days = getattr(settings, "analytics_retention_days", 180)
        except Exception:
            retention_days = 180

    try:
        logger.info("Starting analytics retention purge (%d days)…", retention_days)
        counts = _run_purge(retention_days)
        logger.info("Analytics retention purge complete: %s", counts)
        return {"status": "ok", "deleted": counts}
    except Exception as exc:
        logger.exception("Analytics retention purge failed, retrying…")
        raise self.retry(exc=exc)
