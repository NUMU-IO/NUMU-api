"""Permission repository implementation."""

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.infrastructure.database.models.public.permission import PermissionModel


class PermissionRepository:
    """Permission repository for catalog management."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get_by_id(self, permission_id: UUID) -> PermissionModel | None:
        """Get permission by ID."""
        result = await self.session.execute(
            select(PermissionModel).where(PermissionModel.id == permission_id)
        )
        return result.scalar_one_or_none()

    async def get_by_code(self, code: str) -> PermissionModel | None:
        """Get permission by code."""
        result = await self.session.execute(
            select(PermissionModel).where(PermissionModel.code == code)
        )
        return result.scalar_one_or_none()

    async def get_by_codes(self, codes: list[str]) -> list[PermissionModel]:
        """Get permissions by codes."""
        result = await self.session.execute(
            select(PermissionModel).where(PermissionModel.code.in_(codes))
        )
        return list(result.scalars().all())

    async def get_by_domain(self, domain: str) -> list[PermissionModel]:
        """Get all permissions in a domain."""
        result = await self.session.execute(
            select(PermissionModel).where(PermissionModel.domain == domain)
        )
        return list(result.scalars().all())

    async def get_all(self) -> list[PermissionModel]:
        """Get all permissions."""
        result = await self.session.execute(select(PermissionModel))
        return list(result.scalars().all())

    async def get_app_permissions(self, plugin_id: UUID) -> list[PermissionModel]:
        """Get permissions for a plugin."""
        result = await self.session.execute(
            select(PermissionModel).where(
                PermissionModel.plugin_id == plugin_id,
                PermissionModel.is_app.is_(True),
            )
        )
        return list(result.scalars().all())

    async def create(self, permission: PermissionModel) -> PermissionModel:
        """Create a new permission."""
        self.session.add(permission)
        await self.session.flush()
        await self.session.refresh(permission)
        return permission
