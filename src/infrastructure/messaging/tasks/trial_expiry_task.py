"""Celery task — transition expired trials to read-only state.

Stream 4.6 of the NUMU plan. Trial tenants get 30 days. After expiry
without conversion, they transition to read_only for another 30 days
(the grace period). Existing orders continue to fulfill; new orders
are blocked. Runs hourly via Celery Beat.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime, timedelta

from src.infrastructure.messaging.celery_app import celery_app

logger = logging.getLogger(__name__)

READ_ONLY_GRACE_DAYS = 30


def _run_expiry(batch_size: int) -> dict:
    return asyncio.run(_async_expiry(batch_size))


async def _async_expiry(batch_size: int) -> dict:
    from src.infrastructure.database.connection import (
        AsyncSessionLocal as async_session_factory,
    )
    from src.infrastructure.tenancy.repository import TenantRepository

    transitioned = 0
    errors = 0
    now = datetime.now(UTC)

    async with async_session_factory() as session:
        repo = TenantRepository(session)
        expired_trials = await repo.find_expired_trials(limit=batch_size)

        for tenant in expired_trials:
            try:
                tenant.lifecycle_state = "read_only"
                tenant.read_only_at = now
                tenant.delete_at = now + timedelta(days=READ_ONLY_GRACE_DAYS)
                tenant.expires_at = None  # clear so we stop picking it up
                await repo.update(tenant)
                transitioned += 1
                logger.info(
                    "trial_expired_to_read_only",
                    extra={
                        "tenant_id": str(tenant.id),
                        "subdomain": tenant.subdomain,
                        "delete_at": tenant.delete_at.isoformat(),
                    },
                )
            except Exception:
                errors += 1
                logger.exception(
                    "trial_expiry_transition_failed",
                    extra={"tenant_id": str(tenant.id)},
                )

        await session.commit()

    return {"transitioned": transitioned, "errors": errors}


@celery_app.task(
    name="tasks.expire_trials",
    bind=True,
    max_retries=2,
    default_retry_delay=300,
    acks_late=True,
)
def expire_trials(self, batch_size: int = 100) -> dict:
    """Transition expired trial tenants to read-only."""
    try:
        logger.info("Starting trial expiry sweep …")
        result = _run_expiry(batch_size)
        logger.info("Trial expiry sweep complete: %s", result)
        return {"status": "ok", **result}
    except Exception as exc:
        logger.exception("Trial expiry sweep failed, retrying …")
        raise self.retry(exc=exc)
