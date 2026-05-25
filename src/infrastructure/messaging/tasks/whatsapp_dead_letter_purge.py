"""90-day dead-letter purge (FR-035a / US6).

Beat-scheduled daily at 03:00 UTC. Cross-tenant administrative scan:
selects dead-letter rows where ``created_at < NOW() - INTERVAL '90 days'``
and deletes them in batches. ``message_logs`` (the long-term audit
surface) is NOT touched — only the DLQ replay surface is purged.

03:00 UTC = 05:00 Cairo time, off-peak for the Egyptian commerce
market. Daily run keeps each purge batch small (typical day adds
<100 DLQ rows in steady state).
"""

import asyncio
from datetime import UTC, datetime, timedelta
from typing import Any

from src.config.logging_config import get_logger
from src.infrastructure.messaging.celery_app import celery_app

logger = get_logger(__name__)

_task_loop: asyncio.AbstractEventLoop | None = None

# FR-035a — 90 days from creation.
_RETENTION_DAYS = 90


def _run_async(coro: Any) -> Any:
    global _task_loop
    if _task_loop is None or _task_loop.is_closed():
        _task_loop = asyncio.new_event_loop()
        asyncio.set_event_loop(_task_loop)
    return _task_loop.run_until_complete(coro)


@celery_app.task(
    name="numu_api.whatsapp.purge_dead_letters",
    bind=True,
    max_retries=1,
    default_retry_delay=300,
    soft_time_limit=600,
)
def purge_dead_letters_task(self) -> dict[str, int]:
    """Beat-scheduled (daily at 03:00 UTC)."""
    try:
        return _run_async(_purge_all())
    except Exception as exc:
        logger.error("dead_letter_purge_failed", error=str(exc), exc_info=True)
        raise self.retry(exc=exc)


async def _purge_all() -> dict[str, int]:
    """Cross-tenant scan + per-tenant purge.

    Uses ``RLSBypassContext`` because this is an admin-level operation
    spanning every tenant; the purge itself is age-based, not tenant-
    scoped business logic.
    """
    from sqlalchemy import select

    from src.infrastructure.database.connection import AsyncSessionLocal
    from src.infrastructure.database.models.tenant.whatsapp_dead_letter import (
        WhatsAppDeadLetterModel,
    )
    from src.infrastructure.repositories.whatsapp_dead_letter_repository import (
        WhatsAppDeadLetterRepository,
    )
    from src.infrastructure.tenancy.rls import RLSBypassContext

    cutoff = datetime.now(UTC) - timedelta(days=_RETENTION_DAYS)
    stats: dict[str, int] = {"purged": 0, "tenants_scanned": 0}

    async with AsyncSessionLocal() as session:
        async with RLSBypassContext(session):
            # Group purge work by tenant so each repo call stays under
            # one tenant's logical scope. We use bypass throughout for
            # the actual DELETE since the rule is age-based.
            tenant_rows = (
                await session.execute(
                    select(WhatsAppDeadLetterModel.tenant_id)
                    .where(WhatsAppDeadLetterModel.created_at < cutoff)
                    .distinct()
                )
            ).all()
            tenant_ids = [row[0] for row in tenant_rows]
            stats["tenants_scanned"] = len(tenant_ids)

            repo = WhatsAppDeadLetterRepository(session)
            purged = await repo.purge_older_than(cutoff, batch_size=1000)
            await session.commit()
            stats["purged"] = purged

    logger.info("dead_letter_purge_done", **stats, cutoff=cutoff.isoformat())
    return stats
