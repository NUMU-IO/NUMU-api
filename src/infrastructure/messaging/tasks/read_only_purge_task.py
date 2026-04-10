"""Celery task — hard-delete read-only tenants past their grace period.

Stream 4.6 of the NUMU plan. Read-only tenants have a 30-day grace period
(set by the trial expiry task or cancellation flow). After ``delete_at``
passes, they are permanently deleted. Runs every 6 hours via Celery Beat.
"""

from __future__ import annotations

import asyncio
import logging

from src.infrastructure.messaging.celery_app import celery_app

logger = logging.getLogger(__name__)


def _run_purge(batch_size: int) -> dict:
    return asyncio.run(_async_purge(batch_size))


async def _async_purge(batch_size: int) -> dict:
    from sqlalchemy import select

    from src.infrastructure.database.connection import (
        AsyncSessionLocal as async_session_factory,
    )
    from src.infrastructure.database.models import UserModel
    from src.infrastructure.tenancy.repository import TenantRepository

    deleted = 0
    errors = 0

    async with async_session_factory() as session:
        repo = TenantRepository(session)
        purgeable = await repo.find_purgeable_read_only(limit=batch_size)

        for tenant in purgeable:
            try:
                tenant_id = tenant.id
                subdomain = tenant.subdomain
                owner_id = tenant.owner_id

                await session.delete(tenant)
                await session.flush()

                # Clean up orphaned owner user if it's a demo-converted
                # or trial-only user with no other tenants
                if owner_id:
                    user_q = select(UserModel).where(UserModel.id == owner_id)
                    user = (await session.execute(user_q)).scalar_one_or_none()
                    if user and str(user.email).endswith("@demo.numu.local"):
                        await session.delete(user)

                deleted += 1
                logger.info(
                    "read_only_tenant_purged",
                    extra={"tenant_id": str(tenant_id), "subdomain": subdomain},
                )
            except Exception:
                errors += 1
                logger.exception(
                    "read_only_tenant_purge_failed", extra={"tenant_id": str(tenant.id)}
                )

        await session.commit()

    return {"deleted": deleted, "errors": errors}


@celery_app.task(
    name="tasks.purge_read_only_tenants",
    bind=True,
    max_retries=2,
    default_retry_delay=300,
    acks_late=True,
)
def purge_read_only_tenants(self, batch_size: int = 100) -> dict:
    """Hard-delete read-only tenants past their delete_at deadline."""
    try:
        logger.info("Starting read-only tenant purge …")
        result = _run_purge(batch_size)
        logger.info("Read-only purge complete: %s", result)
        return {"status": "ok", **result}
    except Exception as exc:
        logger.exception("Read-only purge failed, retrying …")
        raise self.retry(exc=exc)
