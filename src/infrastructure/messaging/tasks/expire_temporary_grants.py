"""Expire temporary access grants."""

import logging
from datetime import datetime, timedelta
from uuid import UUID as PyUUID

from sqlalchemy import delete, select, update

from src.infrastructure.database.connection import AsyncSessionLocal
from src.infrastructure.database.models.public.temporary_access_grant import (
    TemporaryAccessGrantModel,
)
from src.infrastructure.database.models.public.tenant_membership import (
    TenantMembershipModel,
)
from src.infrastructure.messaging.celery_app import celery_app
from src.infrastructure.tenancy.rls import enable_rls_bypass

logger = logging.getLogger(__name__)


@celery_app.task(name="tasks.expire_temporary_grants")
async def expire_temporary_grants(grant_id: str | None = None) -> dict:
    """Expire temporary access grants that have passed their valid_until time.

    Two callers: the every-15-min beat sweep (``grant_id=None`` → every grant
    past ``valid_until``) and a per-grant task scheduled at the grant's expiry
    ``eta`` by ``handle_temporary_access_granted`` (``grant_id`` set → just that
    grant, and still only if it is actually past ``valid_until`` — a grant
    extended after scheduling is left for a later sweep). Idempotent either way.
    """
    async with AsyncSessionLocal() as db:
        await enable_rls_bypass(db)  # cross-tenant platform sweep
        conditions = [
            TemporaryAccessGrantModel.valid_until < datetime.utcnow(),
            TemporaryAccessGrantModel.revoked_at.is_(None),
        ]
        if grant_id is not None:
            conditions.append(TemporaryAccessGrantModel.id == PyUUID(str(grant_id)))
        result = await db.execute(select(TemporaryAccessGrantModel).where(*conditions))
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


@celery_app.task(name="tasks.expire_access_requests")
async def expire_access_requests() -> dict:
    """Expire pending access requests that have passed their expiry time."""
    async with AsyncSessionLocal() as db:
        await enable_rls_bypass(db)  # cross-tenant platform sweep
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


@celery_app.task(name="tasks.cleanup_staff_sessions")
async def cleanup_staff_sessions() -> dict:
    """Clean up old revoked staff sessions."""
    async with AsyncSessionLocal() as db:
        await enable_rls_bypass(db)  # cross-tenant platform sweep
        from src.infrastructure.database.models.public.staff_session import (
            StaffSessionModel,
        )

        expiry = datetime.utcnow() - timedelta(days=30)
        result = await db.execute(
            delete(StaffSessionModel).where(
                StaffSessionModel.revoked_at < expiry,
            )
        )
        await db.commit()

        return {
            "cleaned": result.rowcount,
            "checked_at": datetime.utcnow().isoformat(),
        }
