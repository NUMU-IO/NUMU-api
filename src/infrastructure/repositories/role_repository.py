"""Role repository implementation."""

from datetime import datetime
from uuid import UUID

from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from src.infrastructure.database.models.public.role import (
    RoleModel,
    RolePermissionModel,
)


class RoleRepository:
    """Repository for role management."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get_by_id(self, role_id: UUID) -> RoleModel | None:
        """Get role by ID."""
        result = await self.session.execute(
            select(RoleModel).where(
                RoleModel.id == role_id,
                RoleModel.deleted_at.is_(None),
            )
        )
        return result.scalar_one_or_none()

    async def get_by_slug(
        self, slug: str, tenant_id: UUID | None = None
    ) -> RoleModel | None:
        """Get role by slug."""
        result = await self.session.execute(
            select(RoleModel).where(
                RoleModel.slug == slug,
                RoleModel.tenant_id == tenant_id,
                RoleModel.deleted_at.is_(None),
            )
        )
        return result.scalar_one_or_none()

    async def get_by_tenant(self, tenant_id: UUID) -> list[RoleModel]:
        """Get all roles for a tenant."""
        result = await self.session.execute(
            select(RoleModel).where(
                RoleModel.tenant_id == tenant_id,
                RoleModel.deleted_at.is_(None),
            )
        )
        return list(result.scalars().all())

    async def get_system_templates(self) -> list[RoleModel]:
        """Get all system role templates."""
        result = await self.session.execute(
            select(RoleModel).where(
                RoleModel.tenant_id.is_(None),
                RoleModel.is_system.is_(True),
                RoleModel.deleted_at.is_(None),
            )
        )
        return list(result.scalars().all())

    async def get_permissions_for_role(
        self, role_id: UUID
    ) -> list[RolePermissionModel]:
        """Get all permissions for a role."""
        result = await self.session.execute(
            select(RolePermissionModel).where(RolePermissionModel.role_id == role_id)
        )
        return list(result.scalars().all())

    async def create(self, role: RoleModel) -> RoleModel:
        """Create a new role."""
        self.session.add(role)
        await self.session.flush()
        await self.session.refresh(role)
        return role

    async def update(self, role: RoleModel) -> RoleModel:
        """Update a role."""
        role.version += 1
        await self.session.flush()
        await self.session.refresh(role)
        return role

    async def soft_delete(self, role_id: UUID) -> bool:
        """Soft delete a role."""
        await self.session.execute(
            update(RoleModel)
            .where(RoleModel.id == role_id)
            .values(deleted_at=datetime.utcnow())
        )
        await self.session.flush()
        return True

    async def clone_role(
        self,
        source_role_id: UUID,
        target_tenant_id: UUID,
        new_slug: str,
        new_name: str,
    ) -> RoleModel:
        """Clone a role to a new tenant."""
        result = await self.session.execute(
            select(RoleModel).where(RoleModel.id == source_role_id)
        )
        source = result.scalar_one_or_none()
        if not source:
            raise ValueError(f"Source role {source_role_id} not found")

        new_role = RoleModel(
            tenant_id=target_tenant_id,
            name=new_name,
            slug=new_slug,
            description=source.description,
            is_system=False,
            is_owner=source.is_owner,
            is_locked=False,
            version=1,
            cloned_from_id=source_role_id,
        )
        self.session.add(new_role)
        await self.session.flush()
        await self.session.refresh(new_role)

        perms_result = await self.session.execute(
            select(RolePermissionModel).where(
                RolePermissionModel.role_id == source_role_id
            )
        )
        source_perms = list(perms_result.scalars().all())
        for perm in source_perms:
            new_rp = RolePermissionModel(
                role_id=new_role.id,
                permission_id=perm.permission_id,
                scope_qualifier=perm.scope_qualifier,
                granted_by_id=perm.granted_by_id,
            )
            self.session.add(new_rp)

        await self.session.flush()
        return new_role

    async def add_permission(
        self,
        role_id: UUID,
        permission_id: UUID,
        scope_qualifier: dict | None = None,
        granted_by_id: UUID | None = None,
    ) -> RolePermissionModel:
        """Add a permission to a role."""
        rp = RolePermissionModel(
            role_id=role_id,
            permission_id=permission_id,
            scope_qualifier=scope_qualifier or {},
            granted_by_id=granted_by_id,
        )
        self.session.add(rp)
        await self.session.flush()
        await self.session.refresh(rp)
        return rp

    async def remove_permission(self, role_id: UUID, permission_id: UUID) -> bool:
        """Remove a permission from a role."""
        await self.session.execute(
            delete(RolePermissionModel).where(
                RolePermissionModel.role_id == role_id,
                RolePermissionModel.permission_id == permission_id,
            )
        )
        await self.session.flush()
        return True
