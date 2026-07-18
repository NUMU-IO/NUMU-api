"""Celery beat tasks for COD Autopilot (004-cod-autopilot).

Three sweeps, cloning the ``cod_auto_rto_task`` pattern (RLS bypass for
the scan, narrow-to-tenant per write — handled inside the service
functions; persistent per-worker event loop; bounded batches):

- ``tasks.cod_autopilot_send_digests`` (hourly :05) — sends the daily
  merchant ship digest to stores whose store-local hour matches their
  configured ``digest_hour`` (US2 / FR-003).
- ``tasks.cod_autopilot_delivery_checks`` (hourly :20) — creates check
  rows for newly-shipped eligible orders, sends due checks, and flips
  answered-out rows to ``response_exhausted`` (US1 / FR-010..FR-012).
- ``tasks.cod_autopilot_assumed_delivered`` (daily 03:30 UTC — AFTER the
  03:00 auto-RTO sweep so RTO precedence is free, FR-018) — closes
  exhausted, unanswered checks as assumed-delivered (US3 / FR-016).

All three are idempotent: unique constraints (one digest per store/day,
one check per order), message_log event tags, and re-checks of current
row/order state before every write.
"""

from __future__ import annotations

import asyncio
import logging

from src.infrastructure.messaging.celery_app import celery_app

logger = logging.getLogger(__name__)

_task_loop: asyncio.AbstractEventLoop | None = None


def _run_async(coro):
    global _task_loop
    if _task_loop is None or _task_loop.is_closed():
        _task_loop = asyncio.new_event_loop()
        asyncio.set_event_loop(_task_loop)
    return _task_loop.run_until_complete(coro)


@celery_app.task(
    name="tasks.cod_autopilot_send_digests",
    bind=True,
    max_retries=2,
    default_retry_delay=300,
)
def cod_autopilot_send_digests_task(self):
    """Hourly: send ship digests for stores at their local digest hour."""
    try:
        stats = _run_async(_send_digests())
        if stats.get("digests_sent") or stats.get("errors"):
            logger.info("autopilot_digest_sweep %s", stats)
        return stats
    except Exception as exc:
        logger.exception("autopilot_digest_sweep_failed")
        raise self.retry(exc=exc)


async def _send_digests() -> dict:
    from src.application.services.cod_autopilot_service import send_daily_digests
    from src.infrastructure.database.connection import AsyncSessionLocal
    from src.infrastructure.tenancy.rls import enable_rls_bypass

    async with AsyncSessionLocal() as session:
        await enable_rls_bypass(session)
        return await send_daily_digests(session)


@celery_app.task(
    name="tasks.cod_autopilot_delivery_checks",
    bind=True,
    max_retries=2,
    default_retry_delay=300,
)
def cod_autopilot_delivery_checks_task(self):
    """Hourly: create + send delivery checks, mark exhausted rows."""
    try:
        stats = _run_async(_delivery_checks())
        if stats.get("created") or stats.get("sent") or stats.get("errors"):
            logger.info("autopilot_check_sweep %s", stats)
        return stats
    except Exception as exc:
        logger.exception("autopilot_check_sweep_failed")
        raise self.retry(exc=exc)


async def _delivery_checks() -> dict:
    from src.application.services.cod_autopilot_service import (
        create_due_checks,
        mark_exhausted,
        send_due_checks,
    )
    from src.infrastructure.database.connection import AsyncSessionLocal
    from src.infrastructure.tenancy.rls import enable_rls_bypass

    async with AsyncSessionLocal() as session:
        await enable_rls_bypass(session)
        created = await create_due_checks(session)
        sent = await send_due_checks(session)
        exhausted = await mark_exhausted(session)
        return {
            "scanned": created.get("scanned", 0),
            "created": created.get("created", 0),
            "due": sent.get("due", 0),
            "sent": sent.get("sent", 0),
            "blocked": sent.get("blocked", 0),
            "newly_exhausted": exhausted,
            "errors": created.get("errors", 0) + sent.get("errors", 0),
        }


@celery_app.task(
    name="tasks.cod_autopilot_assumed_delivered",
    bind=True,
    max_retries=2,
    default_retry_delay=300,
)
def cod_autopilot_assumed_delivered_task(self):
    """Daily (03:30 UTC, after the RTO sweep): close silent orders."""
    try:
        stats = _run_async(_assumed_delivered())
        if stats.get("closed") or stats.get("errors"):
            logger.info("autopilot_assumed_sweep %s", stats)
        return stats
    except Exception as exc:
        logger.exception("autopilot_assumed_sweep_failed")
        raise self.retry(exc=exc)


async def _assumed_delivered() -> dict:
    from src.application.services.cod_autopilot_service import close_assumed_delivered
    from src.infrastructure.database.connection import AsyncSessionLocal
    from src.infrastructure.tenancy.rls import enable_rls_bypass

    async with AsyncSessionLocal() as session:
        await enable_rls_bypass(session)
        return await close_assumed_delivered(session)
