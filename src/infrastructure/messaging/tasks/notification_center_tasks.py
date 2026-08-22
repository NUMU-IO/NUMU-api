"""Merchant notification feed housekeeping.

``tasks.prune_merchant_notifications`` — beat-scheduled nightly (03:45
UTC, off-peak Cairo). Deletes, in batches, feed rows that are:

* read  AND older than ``READ_RETENTION_DAYS``  (90), or
* any   AND older than ``MAX_RETENTION_DAYS``   (180).

The feed is an attention surface, not an audit log — ``order_activities``
and ``audit_logs`` keep the durable history. Cross-tenant admin sweep
(``RLSBypassContext``), same shape as the WhatsApp dead-letter purge.
"""

from __future__ import annotations

import asyncio
import contextlib
from datetime import UTC, datetime, timedelta
from typing import Any

from src.core.logging import get_logger
from src.infrastructure.messaging.celery_app import celery_app

logger = get_logger(__name__)

_task_loop: asyncio.AbstractEventLoop | None = None

READ_RETENTION_DAYS = 90
MAX_RETENTION_DAYS = 180
BATCH_SIZE = 5_000
MAX_BATCHES = 200  # hard stop per run: 1M rows


def _run_async(coro: Any) -> Any:
    global _task_loop
    if _task_loop is None or _task_loop.is_closed():
        _task_loop = asyncio.new_event_loop()
        asyncio.set_event_loop(_task_loop)
    return _task_loop.run_until_complete(coro)


@celery_app.task(
    name="tasks.prune_merchant_notifications",
    bind=True,
    max_retries=1,
    default_retry_delay=300,
    soft_time_limit=900,
)
def prune_merchant_notifications_task(self) -> dict[str, int]:
    """Beat-scheduled (daily at 03:45 UTC)."""
    try:
        return _run_async(prune_merchant_notifications())
    except Exception as exc:
        logger.error("notification_prune_failed", error=str(exc), exc_info=True)
        raise self.retry(exc=exc)


async def prune_merchant_notifications(
    *,
    now: datetime | None = None,
    session_factory: Any = None,
) -> dict[str, int]:
    """Batch-delete expired rows. Returns ``{"read_pruned", "old_pruned"}``."""
    from sqlalchemy import delete, select

    from src.infrastructure.database.connection import AsyncSessionLocal
    from src.infrastructure.database.models.tenant.merchant_notification import (
        MerchantNotificationModel,
    )
    from src.infrastructure.tenancy.rls import RLSBypassContext

    now = now or datetime.now(UTC)
    read_cutoff = now - timedelta(days=READ_RETENTION_DAYS)
    hard_cutoff = now - timedelta(days=MAX_RETENTION_DAYS)
    factory = session_factory or AsyncSessionLocal
    stats = {"read_pruned": 0, "old_pruned": 0}

    async def _batch(session, predicate) -> int:
        ids = (
            (
                await session.execute(
                    select(MerchantNotificationModel.id)
                    .where(predicate)
                    .limit(BATCH_SIZE)
                )
            )
            .scalars()
            .all()
        )
        if not ids:
            return 0
        result = await session.execute(
            delete(MerchantNotificationModel).where(
                MerchantNotificationModel.id.in_(ids)
            )
        )
        await session.commit()
        return result.rowcount or len(ids)

    async with factory() as session:
        # RLS bypass is a Postgres `set_config`; the sqlite test engine has
        # no RLS to bypass, so skip it there.
        bind = session.get_bind()
        use_bypass = getattr(getattr(bind, "dialect", None), "name", "") == "postgresql"
        bypass = RLSBypassContext(session) if use_bypass else contextlib.nullcontext()
        async with bypass:
            for key, predicate in (
                (
                    "read_pruned",
                    (MerchantNotificationModel.read_at.isnot(None))
                    & (MerchantNotificationModel.created_at < read_cutoff),
                ),
                ("old_pruned", MerchantNotificationModel.created_at < hard_cutoff),
            ):
                for _ in range(MAX_BATCHES):
                    n = await _batch(session, predicate)
                    stats[key] += n
                    if n < BATCH_SIZE:
                        break

    logger.info("notification_prune_complete", **stats)
    return stats
