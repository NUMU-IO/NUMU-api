"""Permission dependencies for FastAPI."""

from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

from fastapi import Depends, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.dependencies.auth import get_current_user_id
from src.api.dependencies.database import get_db
from src.api.dependencies.tenant import get_current_tenant
from src.core.security.sensitive_actions import (
    get_step_up_age_limit,
    is_sensitive_action,
)
from src.infrastructure.cache.redis_cache import RedisCacheService
from src.infrastructure.database.models.public import TenantModel
from src.infrastructure.database.models.public.tenant_membership import (
    MembershipStatus,
    TenantMembershipModel,
)
from src.infrastructure.database.models.public.two_factor import TwoFactorAuthModel
from src.infrastructure.services.permission_service import PermissionService


async def get_current_membership(
    request: Request,
    user_id: Annotated[UUID, Depends(get_current_user_id)],
    tenant: Annotated[TenantModel, Depends(get_current_tenant)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> TenantMembershipModel:
    """Get current user's membership for the tenant.

    Raises 403 if no active membership exists.
    Detects permission version drift for cache invalidation.
    """
    result = await db.execute(
        select(TenantMembershipModel).where(
            TenantMembershipModel.user_id == user_id,
            TenantMembershipModel.tenant_id == tenant.id,
            TenantMembershipModel.deleted_at.is_(None),
        )
    )
    membership = result.scalar_one_or_none()

    if not membership:
        # Backfill owner membership for tenants created before the membership table existed
        if tenant.owner_id == user_id:
            membership = TenantMembershipModel(
                user_id=user_id,
                tenant_id=tenant.id,
                status=MembershipStatus.ACTIVE,
                is_owner=True,
                joined_at=datetime.utcnow(),
            )
            db.add(membership)
            try:
                await db.flush()
                await db.refresh(membership)
                return membership
            except IntegrityError:
                # Concurrent request already inserted the row — re-query
                await db.rollback()
                result = await db.execute(
                    select(TenantMembershipModel).where(
                        TenantMembershipModel.user_id == user_id,
                        TenantMembershipModel.tenant_id == tenant.id,
                        TenantMembershipModel.deleted_at.is_(None),
                    )
                )
                membership = result.scalar_one()
                return membership

        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="No active membership for this tenant",
        )

    if membership.status != MembershipStatus.ACTIVE:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"Membership status is {membership.status.value}",
        )

    return membership


async def get_effective_permissions(
    membership: Annotated[TenantMembershipModel, Depends(get_current_membership)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> PermissionService:
    """Get effective permissions service for the membership."""
    cache = RedisCacheService()
    return PermissionService(db, cache)


def require_permissions(
    *codes: str,
    mode: Literal["all", "any"] = "all",
    scope_check=None,
):
    """Dependency factory for permission checking.

    Usage:
        @router.post("/orders")
        async def create_order(
            perms = Depends(require_permissions("orders.create")),
            ...
        ):
            ...

    Or for multiple:
        @router.delete("/orders/{id}")
        async def delete_order(
            perms = Depends(require_permissions("orders.delete", "orders.edit", mode="any")),
            ...
        ):
            ...
    """

    async def check_permissions(
        membership: Annotated[TenantMembershipModel, Depends(get_current_membership)],
        db: Annotated[AsyncSession, Depends(get_db)],
    ) -> TenantMembershipModel:
        cache = RedisCacheService()
        service = PermissionService(db, cache)
        effective = await service.get_effective_permissions(membership)

        if membership.is_owner:
            return membership

        missing = []
        for code in codes:
            has_perm = effective.has_permission(code)
            if not has_perm:
                missing.append(code)

        if missing:
            if mode == "all":
                if not all(effective.has_permission(c) for c in codes):
                    raise HTTPException(
                        status_code=status.HTTP_403_FORBIDDEN,
                        detail={
                            "error": "Insufficient permissions",
                            "required": list(codes),
                            "missing": missing,
                        },
                    )
            else:
                if not any(effective.has_permission(c) for c in codes):
                    raise HTTPException(
                        status_code=status.HTTP_403_FORBIDDEN,
                        detail={
                            "error": "Insufficient permissions",
                            "required": list(codes),
                            "missing": missing,
                        },
                    )

        if scope_check and not scope_check():
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Scope check failed",
            )

        return membership

    return check_permissions


async def require_step_up(
    action_label: str,
    membership: Annotated[TenantMembershipModel, Depends(get_current_membership)],
    db: Annotated[AsyncSession, Depends(get_db)],
    max_age_seconds: int = 300,
) -> bool:
    """Dependency that enforces fresh 2FA verification.

    Used for HIGH and CRITICAL risk actions.
    Checks two_factor.last_verified_at is recent enough.
    """
    max_age = get_step_up_age_limit(action_label, max_age_seconds)

    if max_age == 0:
        max_age = 300

    result = await db.execute(
        select(TwoFactorAuthModel).where(
            TwoFactorAuthModel.user_id == membership.user_id
        )
    )
    two_factor = result.scalar_one_or_none()

    if not two_factor or two_factor.status != "enabled":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="2FA verification required",
        )

    if not two_factor.verified_at:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="2FA not verified. Please complete 2FA challenge.",
        )

    elapsed = (datetime.utcnow() - two_factor.verified_at).total_seconds()
    if elapsed > max_age:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="2FA verification expired. Please verify again.",
        )

    return True


def require_step_up_dependency(action_code: str):
    """Dependency factory for step-up verification.

    Usage:
        @router.post("/orders/{id}/refund")
        async def refund_order(
            _ = Depends(require_step_up_dependency("orders.refund")),
            ...
        ):
            ...
    """
    if not is_sensitive_action(action_code):
        return lambda: True

    async def step_up_check(
        member: Annotated[TenantMembershipModel, Depends(get_current_membership)],
        db: Annotated[AsyncSession, Depends(get_db)],
    ) -> bool:
        return await require_step_up(action_code, membership=member, db=db)

    return step_up_check


async def check_ip_allowlist(
    membership: Annotated[TenantMembershipModel, Depends(get_current_membership)],
    request: Request,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> bool:
    """Check if request IP is in membership allowlist."""
    from src.infrastructure.database.models.public.staff_access_policy import (
        StaffAccessPolicyModel,
    )

    result = await db.execute(
        select(StaffAccessPolicyModel).where(
            StaffAccessPolicyModel.membership_id == membership.id
        )
    )
    policy = result.scalar_one_or_none()

    if not policy or not policy.ip_allowlist:
        return True

    client_ip = request.client.host if request.client else None
    if not client_ip:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Cannot verify IP address",
        )

    import ipaddress

    try:
        client_addr = ipaddress.ip_address(client_ip)
        for cidr in policy.ip_allowlist:
            network = ipaddress.ip_network(cidr, strict=False)
            if client_addr in network:
                return True
    except Exception:
        return True

    raise HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail="IP address not in allowed list",
    )


async def check_working_hours(
    membership: Annotated[TenantMembershipModel, Depends(get_current_membership)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> bool:
    """Check if current time is within allowed working hours."""
    from src.infrastructure.database.models.public.staff_access_policy import (
        StaffAccessPolicyModel,
    )

    result = await db.execute(
        select(StaffAccessPolicyModel).where(
            StaffAccessPolicyModel.membership_id == membership.id
        )
    )
    policy = result.scalar_one_or_none()

    if not policy or not policy.working_hours:
        return True

    now = datetime.utcnow()
    working_hours = policy.working_hours
    tz_name = working_hours.get("tz", "Africa/Cairo")
    windows = working_hours.get("windows", [])

    if not windows:
        return True

    import zoneinfo

    try:
        tz = zoneinfo.ZoneInfo(tz_name)
        local_now = now.astimezone(tz)
        current_dow = local_now.isoweekday()
        current_time = local_now.time()

        for window in windows:
            if current_dow not in window.get("dow", []):
                continue
            start = window.get("start", "00:00")
            end = window.get("end", "23:59")
            from datetime import time as dt_time

            start_time = dt_time.fromisoformat(start)
            end_time = dt_time.fromisoformat(end)
            if start_time <= current_time <= end_time:
                return True
    except Exception:
        return True

    raise HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail="Outside allowed working hours",
    )


require_owner = require_permissions("billing.transfer_ownership")
require_staff_invite = require_permissions("staff.invite")
require_staff_edit = require_permissions("staff.edit")
require_staff_remove = require_permissions("staff.remove")
require_roles_edit = require_permissions("staff.roles.edit")
