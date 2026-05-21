"""Transfer ownership use case."""

from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from src.infrastructure.database.models.public.permission_change_log import (
    PermissionChangeAction,
    PermissionChangeTargetType,
)
from src.infrastructure.repositories.membership_repository import MembershipRepository
from src.infrastructure.repositories.permission_change_log_repository import (
    PermissionChangeLogRepository,
)


async def transfer_ownership(
    db: AsyncSession,
    tenant_id: UUID,
    current_owner_id: UUID,
    new_owner_id: UUID,
    confirmation: str = "TRANSFER",
) -> bool:
    """Transfer tenant ownership to another user.

    Requires typed confirmation "TRANSFER".
    """
    if confirmation != "TRANSFER":
        raise ValueError("Confirmation required: type TRANSFER to confirm")

    membership_repo = MembershipRepository(db)

    new_owner_membership = await membership_repo.get_by_user_tenant(
        new_owner_id, tenant_id
    )
    if not new_owner_membership:
        raise ValueError("New owner must be a member of this tenant")

    current_owner_membership = await membership_repo.get_by_user_tenant(
        current_owner_id, tenant_id
    )
    if not current_owner_membership or not current_owner_membership.is_owner:
        raise ValueError("Current user is not the owner")

    current_owner_membership.is_owner = False
    new_owner_membership.is_owner = True

    await db.flush()

    log_repo = PermissionChangeLogRepository(db)
    await log_repo.create(
        tenant_id=tenant_id,
        actor_user_id=current_owner_id,
        target_type=PermissionChangeTargetType.MEMBERSHIP,
        target_id=new_owner_membership.id,
        action=PermissionChangeAction.OWNERSHIP_TRANSFERRED,
        after={
            "new_owner_id": str(new_owner_id),
            "old_owner_id": str(current_owner_id),
        },
    )

    await membership_repo.bump_version(current_owner_membership.id)
    await membership_repo.bump_version(new_owner_membership.id)

    return True


async def recover_locked_out_tenant(
    db: AsyncSession,
    tenant_id: UUID,
    platform_admin_id: UUID,
) -> bool:
    """Recover a locked-out tenant (platform admin only)."""
    membership_repo = MembershipRepository(db)

    memberships = await membership_repo.get_active_by_tenant(tenant_id)
    owner_memberships = [m for m in memberships if m.is_owner]

    if owner_memberships:
        return False

    new_owner = await membership_repo.get_by_tenant(tenant_id)
    if new_owner:
        new_owner[0].is_owner = True
        await db.flush()
        return True

    return False
