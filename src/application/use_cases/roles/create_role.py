"""Create role use case."""

from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from src.infrastructure.database.models.public.permission_change_log import (
    PermissionChangeAction,
    PermissionChangeTargetType,
)
from src.infrastructure.database.models.public.role import RoleModel
from src.infrastructure.repositories.permission_change_log_repository import (
    PermissionChangeLogRepository,
)
from src.infrastructure.repositories.role_repository import RoleRepository


async def create_role(
    db: AsyncSession,
    tenant_id: UUID,
    name: str,
    slug: str,
    description: str | None = None,
    created_by_id: UUID | None = None,
) -> RoleModel:
    """Create a new custom role for a tenant."""
    repo = RoleRepository(db)

    existing = await repo.get_by_slug(slug, tenant_id)
    if existing:
        raise ValueError(f"Role with slug '{slug}' already exists")

    role = RoleModel(
        tenant_id=tenant_id,
        name=name,
        slug=slug,
        description=description,
        is_system=False,
        is_owner=False,
        is_locked=False,
        version=1,
        created_by_id=created_by_id,
    )

    created = await repo.create(role)

    log_repo = PermissionChangeLogRepository(db)
    await log_repo.create(
        tenant_id=tenant_id,
        actor_user_id=created_by_id,
        target_type=PermissionChangeTargetType.ROLE,
        target_id=created.id,
        action=PermissionChangeAction.CREATED,
        after={"name": name, "slug": slug},
    )

    return created


async def update_role_permissions(
    db: AsyncSession,
    role_id: UUID,
    permission_ids: list[UUID],
    updated_by_id: UUID | None = None,
) -> bool:
    """Update permissions for a role."""
    from sqlalchemy import delete

    role_repo = RoleRepository(db)
    role = await role_repo.get_by_id(role_id)
    if not role:
        raise ValueError(f"Role {role_id} not found")

    if role.is_locked and role.is_system:
        raise ValueError("Cannot modify system role")

    existing_perms = await role_repo.get_permissions_for_role(role_id)
    old_perm_ids = {str(p.permission_id) for p in existing_perms}
    new_perm_ids = {str(p) for p in permission_ids}

    removed = old_perm_ids - new_perm_ids
    added = new_perm_ids - old_perm_ids

    await db.execute(
        delete(RoleModel.permissions).where(  # type: ignore
            RoleModel.id == role_id  # type: ignore
        )
    )

    await db.commit()

    log_repo = PermissionChangeLogRepository(db)
    await log_repo.create(
        tenant_id=role.tenant_id,
        actor_user_id=updated_by_id,
        target_type=PermissionChangeTargetType.ROLE,
        target_id=role_id,
        action=PermissionChangeAction.UPDATED,
        diff={"added": list(added), "removed": list(removed)},
    )

    if role.tenant_id:
        memberships_result = await db.execute(
            select(TenantMembershipModel).where(
                TenantMembershipModel.tenant_id == role.tenant_id
            )
        )
        memberships = list(memberships_result.scalars().all())
        for membership in memberships:
            await MembershipRepository(db).bump_version(membership.id)

    return True


async def clone_role(
    db: AsyncSession,
    source_role_id: UUID,
    target_tenant_id: UUID,
    new_name: str,
    new_slug: str,
    cloned_by_id: UUID | None = None,
) -> RoleModel:
    """Clone a role to a new tenant.

    The merchant hub typically sends ``new_slug = "<source>-copy"``,
    which collides with the ``(tenant_id, slug)`` unique constraint
    the second time the merchant clicks Clone on the same role. Walk
    the ``-2``, ``-3`` … suffix until we find an unused slug so
    repeated clones produce ``admin-copy``, ``admin-copy-2``,
    ``admin-copy-3`` instead of leaking a 500.
    """
    repo = RoleRepository(db)
    base_slug = new_slug
    candidate = base_slug
    suffix = 2
    while await repo.get_by_slug(candidate, tenant_id=target_tenant_id) is not None:
        candidate = f"{base_slug}-{suffix}"
        suffix += 1
        if suffix > 100:
            # Defensive guard — a tenant accumulating 100 copies of the
            # same role almost certainly has a different problem we
            # shouldn't paper over with another rename. Surface as 409.
            from fastapi import HTTPException

            raise HTTPException(
                status_code=409,
                detail=(
                    "Too many clones of this role already exist — "
                    "delete a few before cloning again."
                ),
            )
    return await repo.clone_role(
        source_role_id=source_role_id,
        target_tenant_id=target_tenant_id,
        new_slug=candidate,
        new_name=new_name,
    )


async def delete_role(
    db: AsyncSession,
    role_id: UUID,
    reassign_to_id: UUID | None = None,
    force: bool = False,
    deleted_by_id: UUID | None = None,
) -> bool:
    """Delete a role, optionally reassigning users."""
    from sqlalchemy import select

    from src.infrastructure.database.models.public.membership_override import (
        MembershipRoleModel,
    )

    role_repo = RoleRepository(db)
    role = await role_repo.get_by_id(role_id)
    if not role:
        raise ValueError(f"Role {role_id} not found")

    if role.is_locked:
        raise ValueError("Cannot delete locked role")

    result = await db.execute(
        select(MembershipRoleModel).where(MembershipRoleModel.role_id == role_id)
    )
    role_users = list(result.scalars().all())

    if role_users and not reassign_to_id and not force:
        raise ValueError(
            f"Role has {len(role_users)} users. Provide reassign_to_id or force=true"
        )

    if reassign_to_id:
        for ru in role_users:
            ru.role_id = reassign_to_id

    await role_repo.soft_delete(role_id)

    log_repo = PermissionChangeLogRepository(db)
    await log_repo.create(
        tenant_id=role.tenant_id,
        actor_user_id=deleted_by_id,
        target_type=PermissionChangeTargetType.ROLE,
        target_id=role_id,
        action=PermissionChangeAction.DELETED,
    )

    return True


async def compare_roles(
    db: AsyncSession,
    role_ids: list[UUID],
) -> dict:
    """Compare multiple roles and return permission matrix."""
    role_repo = RoleRepository(db)

    roles = []
    for rid in role_ids:
        role = await role_repo.get_by_id(rid)
        if role:
            roles.append(role)

    result = {"roles": [], "permissions": {}, "matrix": {}}

    for role in roles:
        perms = await role_repo.get_permissions_for_role(role.id)
        perm_codes = [str(p.permission_id) for p in perms]
        result["roles"].append({
            "id": str(role.id),
            "name": role.name,
            "slug": role.slug,
        })
        result["matrix"][str(role.id)] = perm_codes

    return result


from sqlalchemy import select

from src.infrastructure.database.models.public.tenant_membership import (
    TenantMembershipModel,
)
from src.infrastructure.repositories.membership_repository import MembershipRepository
