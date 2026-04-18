"""Invite staff use case."""

from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from src.infrastructure.database.models.public.staff_invitation import (
    StaffInvitationModel,
)
from src.infrastructure.database.models.public.tenant_membership import (
    MembershipStatus,
    TenantMembershipModel,
)
from src.infrastructure.services.invitation_service import InvitationService


async def invite_staff(
    db: AsyncSession,
    tenant_id: UUID,
    email: str,
    invited_by_id: UUID,
    role_ids: list[UUID] | None = None,
    message: str | None = None,
) -> tuple[StaffInvitationModel, str]:
    """Invite a new staff member.

    Returns:
        Tuple of (invitation, raw_token)
    """
    service = InvitationService(db)
    return await service.create_invitation(
        tenant_id=tenant_id,
        email=email,
        invited_by_id=invited_by_id,
        role_ids=role_ids,
        message=message,
    )


async def accept_invitation(
    db: AsyncSession,
    token: str,
    user_id: UUID,
    first_name: str | None = None,
    last_name: str | None = None,
) -> TenantMembershipModel:
    """Accept a staff invitation.

    Returns:
        Created TenantMembershipModel
    """
    service = InvitationService(db)
    return await service.accept_invitation(
        token=token,
        user_id=user_id,
        first_name=first_name,
        last_name=last_name,
    )


async def revoke_staff_access(
    db: AsyncSession,
    membership_id: UUID,
    reason: str | None = None,
    revoked_by_id: UUID | None = None,
) -> bool:
    """Revoke a staff member's access."""
    from src.infrastructure.database.models.public.permission_change_log import (
        PermissionChangeAction,
        PermissionChangeTargetType,
    )
    from src.infrastructure.repositories.membership_repository import (
        MembershipRepository,
    )
    from src.infrastructure.repositories.permission_change_log_repository import (
        PermissionChangeLogRepository,
    )

    repo = MembershipRepository(db)
    await repo.update_status(membership_id, MembershipStatus.REVOKED)

    log_repo = PermissionChangeLogRepository(db)
    await log_repo.create(
        tenant_id=None,
        actor_user_id=revoked_by_id,
        target_type=PermissionChangeTargetType.MEMBERSHIP,
        target_id=membership_id,
        action=PermissionChangeAction.DELETED,
        reason=reason,
    )

    return True
