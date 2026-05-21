"""Roles routes."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Body, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.dependencies.auth import get_current_user_id
from src.api.dependencies.database import get_db
from src.api.dependencies.permissions import require_permissions
from src.api.dependencies.tenant import get_current_tenant
from src.infrastructure.database.models.public import TenantModel
from src.infrastructure.database.models.public.tenant_membership import (
    TenantMembershipModel,
)
from src.infrastructure.repositories.role_repository import RoleRepository

router = APIRouter(prefix="/roles", tags=["roles"])

require_roles_view = require_permissions("staff.view")
require_roles_edit = require_permissions("staff.roles.edit")


@router.get("")
async def list_roles(
    membership: Annotated[TenantMembershipModel, Depends(require_roles_view)],
    db: Annotated[AsyncSession, Depends(get_db)],
    tenant: Annotated[TenantModel, Depends(get_current_tenant)],
):
    """List all roles for the tenant."""
    repo = RoleRepository(db)
    roles = await repo.get_by_tenant(tenant.id)

    return {
        "roles": [
            {
                "id": str(r.id),
                "name": r.name,
                "slug": r.slug,
                "description": r.description,
                "is_system": r.is_system,
                "is_locked": r.is_locked,
                "is_owner": r.is_owner,
                "version": r.version,
            }
            for r in roles
        ]
    }


@router.get("/templates")
async def list_role_templates(
    membership: Annotated[TenantMembershipModel, Depends(require_roles_view)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """List system role templates."""
    repo = RoleRepository(db)
    templates = await repo.get_system_templates()

    return {
        "templates": [
            {
                "id": str(t.id),
                "name": t.name,
                "slug": t.slug,
                "description": t.description,
                "is_owner": t.is_owner,
            }
            for t in templates
        ]
    }


@router.post("/seed-defaults")
async def seed_default_roles(
    membership: Annotated[TenantMembershipModel, Depends(require_roles_edit)],
    db: Annotated[AsyncSession, Depends(get_db)],
    tenant: Annotated[TenantModel, Depends(get_current_tenant)],
):
    """Clone system role templates into this tenant if it has none."""
    from src.infrastructure.tenancy.service import TenantService

    repo = RoleRepository(db)
    existing = await repo.get_by_tenant(tenant.id)
    if existing:
        return {
            "seeded": False,
            "roles": [
                {"id": str(r.id), "name": r.name, "slug": r.slug} for r in existing
            ],
        }

    service = TenantService(db)
    await service._clone_system_role_templates(tenant.id)
    await db.commit()

    roles = await repo.get_by_tenant(tenant.id)
    return {
        "seeded": True,
        "roles": [{"id": str(r.id), "name": r.name, "slug": r.slug} for r in roles],
    }


@router.get("/{role_id}")
async def get_role(
    role_id: UUID,
    membership: Annotated[TenantMembershipModel, Depends(require_roles_view)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """Get a specific role with its permissions."""
    repo = RoleRepository(db)
    role = await repo.get_by_id(role_id)

    if not role:
        raise HTTPException(status_code=404, detail="Role not found")

    perms = await repo.get_permissions_for_role(role_id)

    return {
        "id": str(role.id),
        "name": role.name,
        "slug": role.slug,
        "description": role.description,
        "is_system": role.is_system,
        "is_locked": role.is_locked,
        "version": role.version,
        "permissions": [
            {
                "permission_id": str(p.permission_id),
                "scope_qualifier": p.scope_qualifier,
            }
            for p in perms
        ],
    }


@router.post("")
async def create_role(
    membership: Annotated[TenantMembershipModel, Depends(require_roles_edit)],
    db: Annotated[AsyncSession, Depends(get_db)],
    tenant: Annotated[TenantModel, Depends(get_current_tenant)],
    user_id: Annotated[UUID, Depends(get_current_user_id)],
    name: str = Body(..., embed=True),
    slug: str = Body(..., embed=True),
    description: str | None = Body(None, embed=True),
):
    """Create a new custom role."""
    from src.application.use_cases.roles.create_role import (
        create_role as uc_create_role,
    )

    role = await uc_create_role(
        db=db,
        tenant_id=tenant.id,
        name=name,
        slug=slug,
        description=description,
        created_by_id=user_id,
    )

    return {
        "id": str(role.id),
        "name": role.name,
        "slug": role.slug,
    }


@router.post("/clone")
async def clone_role(
    membership: Annotated[TenantMembershipModel, Depends(require_roles_edit)],
    db: Annotated[AsyncSession, Depends(get_db)],
    tenant: Annotated[TenantModel, Depends(get_current_tenant)],
    user_id: Annotated[UUID, Depends(get_current_user_id)],
    source_role_id: UUID = Body(..., embed=True),
    new_name: str = Body(..., embed=True),
    new_slug: str = Body(..., embed=True),
):
    """Clone a role."""
    from src.application.use_cases.roles.create_role import clone_role as uc_clone_role

    role = await uc_clone_role(
        db=db,
        source_role_id=source_role_id,
        target_tenant_id=tenant.id,
        new_name=new_name,
        new_slug=new_slug,
        cloned_by_id=user_id,
    )

    return {
        "id": str(role.id),
        "name": role.name,
        "slug": role.slug,
    }


@router.patch("/{role_id}")
async def update_role(
    role_id: UUID,
    membership: Annotated[TenantMembershipModel, Depends(require_roles_edit)],
    db: Annotated[AsyncSession, Depends(get_db)],
    user_id: Annotated[UUID, Depends(get_current_user_id)],
    name: str | None = Body(None, embed=True),
    description: str | None = Body(None, embed=True),
):
    """Update a role."""
    repo = RoleRepository(db)
    role = await repo.get_by_id(role_id)

    if not role:
        raise HTTPException(status_code=404, detail="Role not found")

    if role.is_locked:
        raise HTTPException(status_code=400, detail="Cannot modify locked role")

    if name:
        role.name = name
    if description is not None:
        role.description = description

    await repo.update(role)

    return {
        "id": str(role.id),
        "name": role.name,
        "slug": role.slug,
    }


@router.put("/{role_id}/permissions")
async def set_role_permissions(
    role_id: UUID,
    membership: Annotated[TenantMembershipModel, Depends(require_roles_edit)],
    db: Annotated[AsyncSession, Depends(get_db)],
    permission_ids: list[UUID] = Body(..., embed=True),
):
    """Replace the full set of permissions on a role."""
    from sqlalchemy import delete, select

    from src.infrastructure.database.models.public.role import (
        RolePermissionModel,
    )

    repo = RoleRepository(db)
    role = await repo.get_by_id(role_id)
    if not role:
        raise HTTPException(status_code=404, detail="Role not found")
    if role.is_locked:
        raise HTTPException(status_code=400, detail="Cannot modify locked role")

    existing = await db.execute(
        select(RolePermissionModel.permission_id).where(
            RolePermissionModel.role_id == role_id
        )
    )
    current_ids = {row[0] for row in existing.all()}
    target_ids = set(permission_ids)

    to_remove = current_ids - target_ids
    if to_remove:
        await db.execute(
            delete(RolePermissionModel).where(
                RolePermissionModel.role_id == role_id,
                RolePermissionModel.permission_id.in_(to_remove),
            )
        )

    for pid in target_ids - current_ids:
        db.add(
            RolePermissionModel(
                role_id=role_id,
                permission_id=pid,
                scope_qualifier={},
            )
        )

    role.version += 1
    await db.flush()
    await db.commit()

    return {
        "role_id": str(role_id),
        "permission_ids": [str(p) for p in target_ids],
    }


@router.delete("/{role_id}")
async def delete_role(
    role_id: UUID,
    membership: Annotated[TenantMembershipModel, Depends(require_roles_edit)],
    db: Annotated[AsyncSession, Depends(get_db)],
    user_id: Annotated[UUID, Depends(get_current_user_id)],
    reassign_to_id: UUID | None = None,
    force: bool = False,
):
    """Delete a role."""
    from src.application.use_cases.roles.create_role import (
        delete_role as uc_delete_role,
    )

    await uc_delete_role(
        db=db,
        role_id=role_id,
        reassign_to_id=reassign_to_id,
        force=force,
        deleted_by_id=user_id,
    )

    return {"status": "deleted"}


@router.get("/compare")
async def compare_roles(
    role_ids: list[UUID],
    membership: Annotated[TenantMembershipModel, Depends(require_roles_edit)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """Compare multiple roles."""
    from src.application.use_cases.roles.create_role import (
        compare_roles as uc_compare_roles,
    )

    result = await uc_compare_roles(db=db, role_ids=role_ids)
    return result
