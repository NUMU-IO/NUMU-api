"""Staff list routes."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Body, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.dependencies.auth import get_current_user_id
from src.api.dependencies.database import get_db
from src.api.dependencies.permissions import require_permissions
from src.api.dependencies.tenant import get_current_tenant
from src.infrastructure.database.models.public import TenantModel
from src.infrastructure.database.models.public.tenant_membership import (
    TenantMembershipModel,
)

router = APIRouter(prefix="/staff", tags=["staff"])

require_staff_view = require_permissions("staff.view")


@router.get("")
async def list_staff(
    membership: Annotated[TenantMembershipModel, Depends(require_staff_view)],
    db: Annotated[AsyncSession, Depends(get_db)],
    tenant: Annotated[TenantModel, Depends(get_current_tenant)],
):
    """List all staff members for the tenant."""
    from src.infrastructure.database.models.public.membership_override import (
        MembershipRoleModel,
    )
    from src.infrastructure.database.models.public.role import RoleModel
    from src.infrastructure.database.models.public.user import UserModel

    result = await db.execute(
        select(TenantMembershipModel, UserModel)
        .join(UserModel, TenantMembershipModel.user_id == UserModel.id)
        .where(
            TenantMembershipModel.tenant_id == tenant.id,
            TenantMembershipModel.deleted_at.is_(None),
        )
    )
    rows = result.all()

    staff_list = []
    for m, user in rows:
        roles_result = await db.execute(
            select(RoleModel)
            .join(MembershipRoleModel, MembershipRoleModel.role_id == RoleModel.id)
            .where(MembershipRoleModel.membership_id == m.id)
        )
        roles = roles_result.scalars().all()

        staff_list.append({
            "id": str(m.id),
            "user_id": str(m.user_id),
            "email": user.email,
            "first_name": user.first_name,
            "last_name": user.last_name,
            "is_owner": m.is_owner,
            "status": m.status.value,
            "joined_at": m.joined_at.isoformat() if m.joined_at else None,
            "permission_version": m.permission_version,
            "roles": [{"id": str(r.id), "name": r.name, "slug": r.slug} for r in roles],
        })

    return {"staff": staff_list}


@router.get("/me")
async def get_current_staff(
    membership: Annotated[TenantMembershipModel, Depends(require_staff_view)],
    db: Annotated[AsyncSession, Depends(get_db)],
    user_id: Annotated[UUID, Depends(get_current_user_id)],
):
    """Get current user's staff information."""
    from src.infrastructure.repositories.user_repository import UserRepository
    from src.infrastructure.services.permission_service import PermissionService

    user_repo = UserRepository(db)
    user = await user_repo.get_by_id(user_id)

    service = PermissionService(db)
    effective = await service.get_effective_permissions(membership)

    return {
        "membership_id": str(membership.id),
        "user": {
            "id": str(user.id),
            "email": str(user.email),
            "first_name": user.first_name,
            "last_name": user.last_name,
        },
        "is_owner": membership.is_owner,
        "permissions": effective.to_dict(),
    }


@router.get("/{membership_id}")
async def get_staff_member(
    membership_id: UUID,
    membership: Annotated[TenantMembershipModel, Depends(require_staff_view)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """Get a specific staff member."""
    from src.infrastructure.repositories.membership_repository import (
        MembershipRepository,
    )
    from src.infrastructure.repositories.user_repository import UserRepository

    repo = MembershipRepository(db)
    target = await repo.get_by_id(membership_id)

    if not target:
        from fastapi import HTTPException

        raise HTTPException(status_code=404, detail="Staff member not found")

    user_repo = UserRepository(db)
    user = await user_repo.get_by_id(target.user_id)

    service = PermissionService(db)
    effective = await service.get_effective_permissions(target)

    return {
        "id": str(target.id),
        "user": {
            "id": str(user.id),
            "email": str(user.email),
            "first_name": user.first_name,
            "last_name": user.last_name,
        },
        "is_owner": target.is_owner,
        "status": target.status.value,
        "permissions": effective.to_dict(),
    }


@router.delete("/{membership_id}")
async def remove_staff(
    membership_id: UUID,
    membership: Annotated[
        TenantMembershipModel, Depends(require_permissions("staff.remove"))
    ],
    db: Annotated[AsyncSession, Depends(get_db)],
    user_id: Annotated[UUID, Depends(get_current_user_id)],
):
    """Remove a staff member."""
    from src.infrastructure.database.models.public.tenant_membership import (
        MembershipStatus,
    )
    from src.infrastructure.repositories.membership_repository import (
        MembershipRepository,
    )

    target = await MembershipRepository(db).get_by_id(membership_id)
    if not target:
        from fastapi import HTTPException

        raise HTTPException(status_code=404, detail="Staff member not found")

    if target.is_owner:
        from fastapi import HTTPException

        raise HTTPException(
            status_code=400,
            detail="Cannot remove owner. Transfer ownership first.",
        )

    await MembershipRepository(db).update_status(
        membership_id, MembershipStatus.REVOKED
    )

    return {"status": "removed"}


@router.put("/{membership_id}/roles")
async def set_staff_roles(
    membership_id: UUID,
    membership: Annotated[
        TenantMembershipModel, Depends(require_permissions("staff.roles.edit"))
    ],
    db: Annotated[AsyncSession, Depends(get_db)],
    user_id: Annotated[UUID, Depends(get_current_user_id)],
    role_ids: list[UUID] = Body(..., embed=True),
):
    """Replace the set of roles assigned to a staff membership."""
    from src.application.use_cases.staff.assign_role import (
        assign_role,
        revoke_role,
    )
    from src.infrastructure.database.models.public.membership_override import (
        MembershipRoleModel,
    )
    from src.infrastructure.repositories.membership_repository import (
        MembershipRepository,
    )

    target = await MembershipRepository(db).get_by_id(membership_id)
    if not target:
        raise HTTPException(status_code=404, detail="Staff member not found")

    if target.is_owner:
        raise HTTPException(
            status_code=400,
            detail="Cannot modify roles of the owner.",
        )

    existing_result = await db.execute(
        select(MembershipRoleModel.role_id).where(
            MembershipRoleModel.membership_id == membership_id
        )
    )
    existing_role_ids = {row[0] for row in existing_result.all()}
    target_role_ids = set(role_ids)

    for role_id in existing_role_ids - target_role_ids:
        await revoke_role(db, membership_id, role_id, revoked_by_id=user_id)

    for role_id in target_role_ids - existing_role_ids:
        await assign_role(db, membership_id, role_id, assigned_by_id=user_id)

    await db.commit()
    return {"status": "updated", "role_ids": [str(r) for r in target_role_ids]}


from fastapi import HTTPException

from src.infrastructure.services.permission_service import PermissionService
