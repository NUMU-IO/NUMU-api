"""Tenant membership repository implementation."""

from datetime import datetime
from uuid import UUID

from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from src.infrastructure.database.models.public.membership_override import (
    MembershipOverrideModel,
    OverrideEffect,
)
from src.infrastructure.database.models.public.tenant_membership import (
    MembershipStatus,
    TenantMembershipModel,
)


class MembershipRepository:
    """Repository for tenant memberships."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get_by_id(self, membership_id: UUID) -> TenantMembershipModel | None:
        """Get membership by ID."""
        result = await self.session.execute(
            select(TenantMembershipModel).where(
                TenantMembershipModel.id == membership_id,
                TenantMembershipModel.deleted_at.is_(None),
            )
        )
        return result.scalar_one_or_none()

    async def get_by_user_tenant(
        self, user_id: UUID, tenant_id: UUID
    ) -> TenantMembershipModel | None:
        """Get membership by user and tenant."""
        result = await self.session.execute(
            select(TenantMembershipModel).where(
                TenantMembershipModel.user_id == user_id,
                TenantMembershipModel.tenant_id == tenant_id,
                TenantMembershipModel.deleted_at.is_(None),
            )
        )
        return result.scalar_one_or_none()

    async def get_by_tenant(self, tenant_id: UUID) -> list[TenantMembershipModel]:
        """Get all memberships for a tenant."""
        result = await self.session.execute(
            select(TenantMembershipModel).where(
                TenantMembershipModel.tenant_id == tenant_id,
                TenantMembershipModel.deleted_at.is_(None),
            )
        )
        return list(result.scalars().all())

    async def get_active_by_tenant(
        self, tenant_id: UUID
    ) -> list[TenantMembershipModel]:
        """Get all active memberships for a tenant."""
        result = await self.session.execute(
            select(TenantMembershipModel).where(
                TenantMembershipModel.tenant_id == tenant_id,
                TenantMembershipModel.status == MembershipStatus.ACTIVE,
                TenantMembershipModel.deleted_at.is_(None),
            )
        )
        return list(result.scalars().all())

    async def get_by_user(self, user_id: UUID) -> list[TenantMembershipModel]:
        """Get all memberships for a user."""
        result = await self.session.execute(
            select(TenantMembershipModel).where(
                TenantMembershipModel.user_id == user_id,
                TenantMembershipModel.deleted_at.is_(None),
            )
        )
        return list(result.scalars().all())

    async def create(self, membership: TenantMembershipModel) -> TenantMembershipModel:
        """Create a new membership."""
        self.session.add(membership)
        await self.session.flush()
        await self.session.refresh(membership)
        return membership

    async def update(self, membership: TenantMembershipModel) -> TenantMembershipModel:
        """Update a membership."""
        await self.session.flush()
        await self.session.refresh(membership)
        return membership

    async def delete(self, membership_id: UUID) -> bool:
        """Soft delete a membership."""
        await self.session.execute(
            update(TenantMembershipModel)
            .where(TenantMembershipModel.id == membership_id)
            .values(deleted_at=datetime.utcnow())
        )
        await self.session.flush()
        return True

    async def update_status(
        self, membership_id: UUID, status: MembershipStatus
    ) -> bool:
        """Update membership status."""
        await self.session.execute(
            update(TenantMembershipModel)
            .where(TenantMembershipModel.id == membership_id)
            .values(status=status)
        )
        await self.session.flush()
        return True

    async def bump_version(self, membership_id: UUID) -> int:
        """Bump permission version and return new version."""
        result = await self.session.execute(
            select(TenantMembershipModel).where(
                TenantMembershipModel.id == membership_id
            )
        )
        membership = result.scalar_one_or_none()
        if membership:
            membership.permission_version += 1
            await self.session.flush()
            return membership.permission_version
        return 0


class OverrideRepository:
    """Repository for membership permission overrides."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get_by_membership(
        self, membership_id: UUID
    ) -> list[MembershipOverrideModel]:
        """Get all overrides for a membership."""
        result = await self.session.execute(
            select(MembershipOverrideModel).where(
                MembershipOverrideModel.membership_id == membership_id,
                or_(
                    MembershipOverrideModel.expires_at.is_(None),
                    MembershipOverrideModel.expires_at > datetime.utcnow(),
                ),
            )
        )
        return list(result.scalars().all())

    async def get_allow_overrides(
        self, membership_id: UUID
    ) -> list[MembershipOverrideModel]:
        """Get ALLOW overrides for a membership."""
        result = await self.session.execute(
            select(MembershipOverrideModel).where(
                MembershipOverrideModel.membership_id == membership_id,
                MembershipOverrideModel.effect == OverrideEffect.ALLOW,
                or_(
                    MembershipOverrideModel.expires_at.is_(None),
                    MembershipOverrideModel.expires_at > datetime.utcnow(),
                ),
            )
        )
        return list(result.scalars().all())

    async def get_deny_overrides(
        self, membership_id: UUID
    ) -> list[MembershipOverrideModel]:
        """Get DENY overrides for a membership."""
        result = await self.session.execute(
            select(MembershipOverrideModel).where(
                MembershipOverrideModel.membership_id == membership_id,
                MembershipOverrideModel.effect == OverrideEffect.DENY,
                or_(
                    MembershipOverrideModel.expires_at.is_(None),
                    MembershipOverrideModel.expires_at > datetime.utcnow(),
                ),
            )
        )
        return list(result.scalars().all())

    async def create(
        self, override: MembershipOverrideModel
    ) -> MembershipOverrideModel:
        """Create a new override."""
        self.session.add(override)
        await self.session.flush()
        await self.session.refresh(override)
        return override

    async def delete(self, override_id: UUID) -> bool:
        """Delete an override."""
        await self.session.execute(
            delete(MembershipOverrideModel).where(
                MembershipOverrideModel.id == override_id
            )
        )
        await self.session.flush()
        return True

    async def delete_by_membership_permission(
        self, membership_id: UUID, permission_id: UUID
    ) -> bool:
        """Delete all overrides for a membership and permission."""
        await self.session.execute(
            delete(MembershipOverrideModel).where(
                MembershipOverrideModel.membership_id == membership_id,
                MembershipOverrideModel.permission_id == permission_id,
            )
        )
        await self.session.flush()
        return True


from sqlalchemy import or_
