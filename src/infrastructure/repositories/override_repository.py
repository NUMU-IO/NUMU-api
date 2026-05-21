"""Membership override repository implementation."""

from datetime import datetime
from uuid import UUID

from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from src.infrastructure.database.models.public.membership_override import (
    MembershipOverrideModel,
    OverrideEffect,
)


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
                MembershipOverrideModel.membership_id == membership_id
            )
        )
        return list(result.scalars().all())

    async def get_by_membership_permission(
        self, membership_id: UUID, permission_id: UUID
    ) -> MembershipOverrideModel | None:
        """Get specific override for membership and permission."""
        result = await self.session.execute(
            select(MembershipOverrideModel).where(
                MembershipOverrideModel.membership_id == membership_id,
                MembershipOverrideModel.permission_id == permission_id,
            )
        )
        return result.scalar_one_or_none()

    async def create(
        self, override: MembershipOverrideModel
    ) -> MembershipOverrideModel:
        """Create a new override."""
        self.session.add(override)
        await self.session.flush()
        await self.session.refresh(override)
        return override

    async def set_override(
        self,
        membership_id: UUID,
        permission_id: UUID,
        effect: OverrideEffect,
        granted_by_id: UUID | None = None,
        reason: str | None = None,
        expires_at: datetime | None = None,
        scope_qualifier: dict | None = None,
    ) -> MembershipOverrideModel:
        """Set (create or update) an override."""
        existing = await self.get_by_membership_permission(membership_id, permission_id)
        if existing:
            await self.session.execute(
                update(MembershipOverrideModel)
                .where(MembershipOverrideModel.id == existing.id)
                .values(
                    effect=effect,
                    granted_by_id=granted_by_id,
                    reason=reason,
                    expires_at=expires_at,
                    scope_qualifier=scope_qualifier or {},
                    updated_at=datetime.utcnow(),
                )
            )
            await self.session.refresh(existing)
            return existing

        override = MembershipOverrideModel(
            membership_id=membership_id,
            permission_id=permission_id,
            effect=effect,
            granted_by_id=granted_by_id,
            reason=reason,
            expires_at=expires_at,
            scope_qualifier=scope_qualifier or {},
        )
        self.session.add(override)
        await self.session.flush()
        await self.session.refresh(override)
        return override

    async def clear_override(self, membership_id: UUID, permission_id: UUID) -> bool:
        """Clear (delete) an override."""
        result = await self.session.execute(
            delete(MembershipOverrideModel).where(
                MembershipOverrideModel.membership_id == membership_id,
                MembershipOverrideModel.permission_id == permission_id,
            )
        )
        return result.rowcount > 0

    async def clear_expired(self) -> int:
        """Clear all expired overrides. Returns count of deleted."""
        result = await self.session.execute(
            delete(MembershipOverrideModel).where(
                MembershipOverrideModel.expires_at.isnot(None),
                MembershipOverrideModel.expires_at < datetime.utcnow(),
            )
        )
        return result.rowcount

    async def clear_by_membership(self, membership_id: UUID) -> int:
        """Clear all overrides for a membership. Returns count of deleted."""
        result = await self.session.execute(
            delete(MembershipOverrideModel).where(
                MembershipOverrideModel.membership_id == membership_id
            )
        )
        return result.rowcount
