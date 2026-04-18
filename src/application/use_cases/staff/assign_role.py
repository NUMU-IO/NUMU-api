"""Assign role to staff use case."""

from datetime import datetime
from uuid import UUID

from sqlalchemy import delete, update
from sqlalchemy.ext.asyncio import AsyncSession

from src.infrastructure.database.models.public.membership_override import (
    MembershipRoleModel,
)
from src.infrastructure.database.models.public.permission_change_log import (
    PermissionChangeAction,
    PermissionChangeTargetType,
)
from src.infrastructure.database.models.public.tenant_membership import (
    TenantMembershipModel,
)
from src.infrastructure.repositories.membership_repository import MembershipRepository
from src.infrastructure.repositories.permission_change_log_repository import (
    PermissionChangeLogRepository,
)
from src.infrastructure.repositories.role_repository import RoleRepository


async def assign_role(
    db: AsyncSession,
    membership_id: UUID,
    role_id: UUID,
    assigned_by_id: UUID | None = None,
) -> bool:
    """Assign a role to a staff membership."""
    role_repo = RoleRepository(db)
    role = await role_repo.get_by_id(role_id)
    if not role:
        raise ValueError(f"Role {role_id} not found")

    membership_repo = MembershipRepository(db)
    membership = await membership_repo.get_by_id(membership_id)
    if not membership:
        raise ValueError(f"Membership {membership_id} not found")

    mr = MembershipRoleModel(
        membership_id=membership_id,
        role_id=role_id,
        assigned_by_id=assigned_by_id,
    )
    db.add(mr)
    await db.flush()
    await db.refresh(mr)

    log_repo = PermissionChangeLogRepository(db)
    await log_repo.create(
        tenant_id=membership.tenant_id,
        actor_user_id=assigned_by_id,
        target_type=PermissionChangeTargetType.MEMBERSHIP,
        target_id=membership_id,
        action=PermissionChangeAction.ROLE_ASSIGNED,
        after={"role_id": str(role_id), "role_name": role.name},
    )

    await db.execute(
        update(TenantMembershipModel)
        .where(TenantMembershipModel.id == membership_id)
        .values(
            permission_version=TenantMembershipModel.permission_version + 1,
            updated_at=datetime.utcnow(),
        )
    )

    from src.core.events.staff_events import StaffRoleAssignedEvent
    from src.infrastructure.events.setup import get_event_bus

    event_bus = get_event_bus()
    await event_bus.publish(
        StaffRoleAssignedEvent(
            membership_id=str(membership_id),
            role_id=str(role_id),
            assigned_by_id=str(assigned_by_id) if assigned_by_id else None,
        )
    )

    return True


async def revoke_role(
    db: AsyncSession,
    membership_id: UUID,
    role_id: UUID,
    revoked_by_id: UUID | None = None,
) -> bool:
    """Revoke a role from a staff membership."""
    role_repo = RoleRepository(db)
    role = await role_repo.get_by_id(role_id)
    if not role:
        raise ValueError(f"Role {role_id} not found")

    membership_repo = MembershipRepository(db)
    membership = await membership_repo.get_by_id(membership_id)
    if not membership:
        raise ValueError(f"Membership {membership_id} not found")

    await db.execute(
        delete(MembershipRoleModel).where(
            MembershipRoleModel.membership_id == membership_id,
            MembershipRoleModel.role_id == role_id,
        )
    )
    await db.flush()

    log_repo = PermissionChangeLogRepository(db)
    await log_repo.create(
        tenant_id=membership.tenant_id,
        actor_user_id=revoked_by_id,
        target_type=PermissionChangeTargetType.MEMBERSHIP,
        target_id=membership_id,
        action=PermissionChangeAction.ROLE_REVOKED,
        before={"role_id": str(role_id), "role_name": role.name},
    )

    await db.execute(
        update(TenantMembershipModel)
        .where(TenantMembershipModel.id == membership_id)
        .values(
            permission_version=TenantMembershipModel.permission_version + 1,
            updated_at=datetime.utcnow(),
        )
    )

    from src.core.events.staff_events import StaffRoleRevokedEvent
    from src.infrastructure.events.setup import get_event_bus

    event_bus = get_event_bus()
    await event_bus.publish(
        StaffRoleRevokedEvent(
            membership_id=str(membership_id),
            role_id=str(role_id),
            revoked_by_id=str(revoked_by_id) if revoked_by_id else None,
        )
    )

    return True
