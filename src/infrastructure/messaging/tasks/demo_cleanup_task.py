"""Celery task — hard-delete expired demo tenants.

Stream 1.5 of the NUMU plan. Demo tenants have a 7-day window. After
expiry, they are deleted outright (no read-only grace period). Runs
every 2 hours via Celery Beat.

Cascade: deletes tenant → stores → products → orders → customers →
onboarding → the ephemeral demo user (owner). All of this is handled
by SQLAlchemy cascade rules at the model level. If any cascade is
missing, the task logs the error and moves on to the next tenant.
"""

from __future__ import annotations

import asyncio
import logging

from src.infrastructure.messaging.celery_app import celery_app

logger = logging.getLogger(__name__)


def _run_cleanup(batch_size: int) -> dict:
    return asyncio.run(_async_cleanup(batch_size))


async def _async_cleanup(batch_size: int) -> dict:
    from src.infrastructure.database.connection import (
        AsyncSessionLocal as async_session_factory,
    )
    from src.infrastructure.tenancy.repository import TenantRepository

    deleted = 0
    errors = 0

    async with async_session_factory() as session:
        repo = TenantRepository(session)
        expired_demos = await repo.find_expired_demos(limit=batch_size)

        for tenant in expired_demos:
            try:
                tenant_id = tenant.id
                subdomain = tenant.subdomain
                owner_id = tenant.owner_id

                # Delete the tenant row — cascading FKs handle stores/etc.
                await session.delete(tenant)
                await session.flush()

                # Delete the ephemeral demo user if present
                if owner_id:
                    from sqlalchemy import select

                    from src.infrastructure.database.models import UserModel

                    user_q = select(UserModel).where(UserModel.id == owner_id)
                    user = (await session.execute(user_q)).scalar_one_or_none()
                    if user and str(user.email).endswith("@demo.numu.local"):
                        await session.delete(user)

                deleted += 1
                logger.info(
                    "demo_tenant_deleted",
                    extra={
                        "tenant_id": str(tenant_id),
                        "subdomain": subdomain,
                    },
                )
            except Exception:
                errors += 1
                logger.exception(
                    "demo_tenant_delete_failed",
                    extra={"tenant_id": str(tenant.id)},
                )

        await session.commit()

    return {"deleted": deleted, "errors": errors}


@celery_app.task(
    name="tasks.cleanup_expired_demo_tenants",
    bind=True,
    max_retries=2,
    default_retry_delay=300,
    acks_late=True,
)
def cleanup_expired_demo_tenants(self, batch_size: int = 100) -> dict:
    """Delete demo tenants whose 7-day window has elapsed."""
    try:
        logger.info("Starting demo tenant cleanup …")
        result = _run_cleanup(batch_size)
        logger.info("Demo cleanup complete: %s", result)
        return {"status": "ok", **result}
    except Exception as exc:
        logger.exception("Demo cleanup failed, retrying …")
        raise self.retry(exc=exc)
