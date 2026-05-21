"""Expire temporary access grants."""

import logging
from datetime import datetime, timedelta

from sqlalchemy import select, update

from src.infrastructure.database.connection import get_db_session
from src.infrastructure.database.models.public.temporary_access_grant import (
    TemporaryAccessGrantModel,
)
from src.infrastructure.database.models.public.tenant_membership import (
    TenantMembershipModel,
)
from src.infrastructure.messaging.celery_app import celery_app

logger = logging.getLogger(__name__)


@celery_app.task(name="expire_temporary_grants")
async def expire_temporary_grants() -> dict:
    """Expire temporary access grants that have passed their valid_until time."""
    async with get_db_session() as db:
        result = await db.execute(
            select(TemporaryAccessGrantModel).where(
                TemporaryAccessGrantModel.valid_until < datetime.utcnow(),
                TemporaryAccessGrantModel.revoked_at.is_(None),
            )
        )
        grants = list(result.scalars().all())

        expired_count = 0
        for grant in grants:
            grant.revoked_at = datetime.utcnow()

            mem_result = await db.execute(
                select(TenantMembershipModel).where(
                    TenantMembershipModel.id == grant.membership_id
                )
            )
            membership = mem_result.scalar_one_or_none()
            if membership:
                membership.permission_version += 1
                expired_count += 1

        await db.commit()

        return {
            "expired": expired_count,
            "checked_at": datetime.utcnow().isoformat(),
        }


@celery_app.task(name="expire_access_requests")
async def expire_access_requests() -> dict:
    """Expire pending access requests that have passed their expiry time."""
    async with get_db_session() as db:
        from src.infrastructure.database.models.public.access_request import (
            AccessRequestModel,
            AccessRequestStatus,
        )

        result = await db.execute(
            update(AccessRequestModel)
            .where(
                AccessRequestModel.status == AccessRequestStatus.PENDING,
                AccessRequestModel.expires_at < datetime.utcnow(),
            )
            .values(status=AccessRequestStatus.EXPIRED)
        )

        await db.commit()

        return {
            "expired": result.rowcount,
            "checked_at": datetime.utcnow().isoformat(),
        }


@celery_app.task(name="cleanup_staff_sessions")
async def cleanup_staff_sessions() -> dict:
    """Clean up old revoked staff sessions."""
    async with get_db_session() as db:
        from src.infrastructure.database.models.public.staff_session import (
            StaffSessionModel,
        )

        expiry = datetime.utcnow() - timedelta(days=30)
        result = await db.execute(
            update(StaffSessionModel).where(
                StaffSessionModel.revoked_at < expiry,
            )
        )
        await db.commit()

        return {
            "cleaned": result.rowcount,
            "checked_at": datetime.utcnow().isoformat(),
        }
