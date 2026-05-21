"""Staff event handlers for the event bus."""

import logging

from src.infrastructure.cache.redis_cache import RedisCacheService

logger = logging.getLogger(__name__)


async def handle_staff_invited(event) -> None:
    """Handle staff invitation - send email notification.

    Note: staff invites are tenant-level (no `store_id`), so the
    merchant per-store email-template override does not apply here —
    the renderer skips lookup when ``store_id is None`` and falls
    through to the registry default. ``tenant_id`` is forwarded into
    the email-context so audit logs / future tenant-level overrides
    have the attribution.
    """
    from src.infrastructure.messaging.tasks.notification_tasks import send_email_task

    logger.info(f"staff_invited: {event.email} tenant={event.tenant_id}")
    send_email_task.delay(
        to=event.email,
        subject="You've been invited",
        template="staff_invitation",
        context={
            "invitation_id": event.invitation_id,
            "tenant_id": event.tenant_id,
            "role_ids": event.role_ids,
        },
    )


async def handle_staff_activated(event) -> None:
    """Handle staff activation - invalidate cache, log activity."""
    logger.info(f"staff_activated: membership={event.membership_id}")

    cache = RedisCacheService()
    await cache.invalidate(f"perms:{event.membership_id}")
    await cache.invalidate(f"effective:{event.membership_id}")

    await _log_activity(
        event.tenant_id,
        event.user_id,
        "staff.activated",
        {"membership_id": event.membership_id, "role_ids": event.role_ids},
    )


async def handle_staff_role_assigned(event) -> None:
    """Handle role assignment - invalidate cache, log activity."""
    logger.info(f"role_assigned: membership={event.membership_id} role={event.role_id}")

    cache = RedisCacheService()
    await cache.invalidate(f"perms:{event.membership_id}")
    await cache.invalidate(f"effective:{event.membership_id}")

    tenant_id = await _get_membership_tenant(event.membership_id)
    if tenant_id:
        await _log_activity(
            tenant_id,
            event.assigned_by_id,
            "staff.role_assigned",
            {"membership_id": event.membership_id, "role_id": event.role_id},
        )


async def handle_staff_role_revoked(event) -> None:
    """Handle role revocation - invalidate cache, log activity."""
    logger.info(f"role_revoked: membership={event.membership_id} role={event.role_id}")

    cache = RedisCacheService()
    await cache.invalidate(f"perms:{event.membership_id}")
    await cache.invalidate(f"effective:{event.membership_id}")


async def handle_staff_removed(event) -> None:
    """Handle staff removal - revoke sessions, log activity."""
    from src.infrastructure.database.connection import get_db_session
    from src.infrastructure.repositories.staff_session_repository import (
        StaffSessionRepository,
    )

    logger.info(f"staff_removed: membership={event.membership_id}")

    async with get_db_session() as db:
        session_repo = StaffSessionRepository(db)
        await session_repo.revoke_by_membership(
            event.membership_id, event.removed_by_id
        )

    cache = RedisCacheService()
    await cache.invalidate(f"perms:{event.membership_id}")
    await cache.invalidate(f"effective:{event.membership_id}")


async def handle_override_set(event) -> None:
    """Handle override set - invalidate cache, log activity."""
    logger.info(
        f"override_set: membership={event.membership_id} permission={event.permission_id}"
    )

    cache = RedisCacheService()
    await cache.invalidate(f"perms:{event.membership_id}")
    await cache.invalidate(f"effective:{event.membership_id}")


async def handle_override_cleared(event) -> None:
    """Handle override cleared - invalidate cache."""
    logger.info(f"override_cleared: membership={event.membership_id}")

    cache = RedisCacheService()
    await cache.invalidate(f"perms:{event.membership_id}")
    await cache.invalidate(f"effective:{event.membership_id}")


async def handle_access_request_created(event) -> None:
    """Handle access request created - notify reviewers."""
    from src.infrastructure.messaging.tasks.notification_tasks import send_email_task

    logger.info(f"access_request_created: {event.request_id}")

    reviewer_ids = await _get_reviewers_for_tenant(event.tenant_id)
    for reviewer_id in reviewer_ids:
        send_email_task.delay(
            to=reviewer_id,
            subject="New access request",
            template="access_request_created",
            context={
                "request_id": event.request_id,
                "requester_user_id": event.requester_user_id,
            },
        )


async def handle_access_request_approved(event) -> None:
    """Handle access request approved - notify requester, update cache."""
    from src.infrastructure.messaging.tasks.notification_tasks import send_email_task

    logger.info(f"access_request_approved: {event.request_id}")

    send_email_task.delay(
        to=event.requester_user_id,
        subject="Access request approved",
        template="access_request_approved",
        context={
            "request_id": event.request_id,
            "reviewer_user_id": event.reviewer_user_id,
        },
    )

    membership_id = await _get_membership_by_user(
        event.requester_user_id, event.tenant_id
    )
    if membership_id:
        cache = RedisCacheService()
        await cache.invalidate(f"perms:{membership_id}")
        await cache.invalidate(f"effective:{membership_id}")


async def handle_access_request_denied(event) -> None:
    """Handle access request denied - notify requester."""
    from src.infrastructure.messaging.tasks.notification_tasks import send_email_task

    logger.info(f"access_request_denied: {event.request_id}")

    send_email_task.delay(
        to=event.requester_user_id,
        subject="Access request denied",
        template="access_request_denied",
        context={
            "request_id": event.request_id,
            "reviewer_user_id": event.reviewer_user_id,
        },
    )


async def handle_temporary_access_granted(event) -> None:
    """Handle temporary access granted - schedule expiry, notify user."""
    from src.infrastructure.messaging.celery_app import celery_app
    from src.infrastructure.messaging.tasks.notification_tasks import send_email_task

    logger.info(f"temporary_access_granted: membership={event.membership_id}")

    send_email_task.delay(
        to=event.requester_user_id,
        subject="Temporary access granted",
        template="temporary_access_granted",
        context={
            "permission_ids": event.permission_ids,
            "expires_at": event.expires_at,
        },
    )

    celery_app.send_task(
        "tasks.expire_temporary_grants",
        args=[event.grant_id],
        eta=event.expires_at,
    )


async def handle_temporary_access_revoked(event) -> None:
    """Handle temporary access revoked - invalidate cache."""
    logger.info(f"temporary_access_revoked: membership={event.membership_id}")

    cache = RedisCacheService()
    await cache.invalidate(f"perms:{event.membership_id}")
    await cache.invalidate(f"effective:{event.membership_id}")


async def _log_activity(
    tenant_id: str, user_id: str, action: str, details: dict
) -> None:
    """Log activity to database."""
    from datetime import datetime
    from uuid import uuid4

    from src.infrastructure.database.connection import get_db_session
    from src.infrastructure.database.models.public.permission_change_log import (
        PermissionChangeLogModel,
    )

    async with get_db_session() as db:
        log_entry = PermissionChangeLogModel(
            id=uuid4(),
            tenant_id=tenant_id,
            user_id=user_id,
            action=action,
            details=details,
            created_at=datetime.utcnow(),
        )
        db.add(log_entry)
        await db.commit()


async def _get_membership_tenant(membership_id: str) -> str | None:
    """Get tenant_id for a membership."""
    from uuid import UUID

    from sqlalchemy import select

    from src.infrastructure.database.connection import get_db_session
    from src.infrastructure.database.models.public.tenant_membership import (
        TenantMembershipModel,
    )

    async with get_db_session() as db:
        result = await db.execute(
            select(TenantMembershipModel).where(
                TenantMembershipModel.id == UUID(membership_id)
            )
        )
        membership = result.scalar_one_or_none()
        return str(membership.tenant_id) if membership else None


async def _get_membership_by_user(user_id: str, tenant_id: str) -> str | None:
    """Get membership_id for a user in a tenant."""
    from uuid import UUID

    from sqlalchemy import select

    from src.infrastructure.database.connection import get_db_session
    from src.infrastructure.database.models.public.tenant_membership import (
        TenantMembershipModel,
    )

    async with get_db_session() as db:
        result = await db.execute(
            select(TenantMembershipModel).where(
                TenantMembershipModel.user_id == UUID(user_id),
                TenantMembershipModel.tenant_id == UUID(tenant_id),
            )
        )
        membership = result.scalar_one_or_none()
        return str(membership.id) if membership else None


async def _get_reviewers_for_tenant(tenant_id: str) -> list[str]:
    """Get user IDs of reviewers for a tenant."""
    from uuid import UUID

    from sqlalchemy import select

    from src.infrastructure.database.connection import get_db_session
    from src.infrastructure.database.models.public.tenant_membership import (
        TenantMembershipModel,
    )

    async with get_db_session() as db:
        result = await db.execute(
            select(TenantMembershipModel).where(
                TenantMembershipModel.tenant_id == UUID(tenant_id),
                TenantMembershipModel.is_owner == True,  # noqa: E712
            )
        )
        memberships = result.scalars().all()
        return [str(m.user_id) for m in memberships]
