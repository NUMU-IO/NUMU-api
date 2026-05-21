"""Staff session repository."""

from datetime import datetime
from uuid import UUID

from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from src.infrastructure.database.models.public.staff_session import StaffSessionModel


class StaffSessionRepository:
    """Repository for staff sessions."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get_by_id(self, session_id: UUID) -> StaffSessionModel | None:
        """Get session by ID."""
        result = await self.session.execute(
            select(StaffSessionModel).where(
                StaffSessionModel.id == session_id,
                StaffSessionModel.revoked_at.is_(None),
            )
        )
        return result.scalar_one_or_none()

    async def get_active_by_tenant(self, tenant_id: UUID) -> list[StaffSessionModel]:
        """Get all active sessions for a tenant."""
        from src.infrastructure.database.models.public.tenant_membership import (
            TenantMembershipModel,
        )

        result = await self.session.execute(
            select(StaffSessionModel)
            .join(
                TenantMembershipModel,
                StaffSessionModel.membership_id == TenantMembershipModel.id,
            )
            .where(
                TenantMembershipModel.tenant_id == tenant_id,
                TenantMembershipModel.deleted_at.is_(None),
                StaffSessionModel.revoked_at.is_(None),
            )
        )
        return list(result.scalars().all())

    async def get_active_by_membership(
        self, membership_id: UUID
    ) -> list[StaffSessionModel]:
        """Get all active sessions for a membership."""
        result = await self.session.execute(
            select(StaffSessionModel).where(
                StaffSessionModel.membership_id == membership_id,
                StaffSessionModel.revoked_at.is_(None),
            )
        )
        return list(result.scalars().all())

    async def create(self, session: StaffSessionModel) -> StaffSessionModel:
        """Create a new session."""
        self.session.add(session)
        await self.session.flush()
        await self.session.refresh(session)
        return session

    async def update_last_seen(self, session_id: UUID) -> None:
        """Update last seen timestamp."""
        await self.session.execute(
            update(StaffSessionModel)
            .where(StaffSessionModel.id == session_id)
            .values(last_seen_at=datetime.utcnow())
        )

    async def revoke(
        self, session_id: UUID, revoked_by_id: UUID, reason: str | None = None
    ) -> bool:
        """Revoke a session."""
        result = await self.session.execute(
            update(StaffSessionModel)
            .where(StaffSessionModel.id == session_id)
            .values(
                revoked_at=datetime.utcnow(),
                revoked_by_id=revoked_by_id,
                revoked_reason=reason,
            )
        )
        return result.rowcount > 0

    async def revoke_by_membership(
        self, membership_id: UUID, revoked_by_id: UUID
    ) -> int:
        """Revoke all sessions for a membership."""
        result = await self.session.execute(
            update(StaffSessionModel)
            .where(
                StaffSessionModel.membership_id == membership_id,
                StaffSessionModel.revoked_at.is_(None),
            )
            .values(
                revoked_at=datetime.utcnow(),
                revoked_by_id=revoked_by_id,
                revoked_reason="revoked by admin",
            )
        )
        return result.rowcount

    async def cleanup_expired(self, max_age_hours: int = 24) -> int:
        """Clean up expired sessions. Returns count of cleaned."""
        from datetime import timedelta

        cutoff = datetime.utcnow() - timedelta(hours=max_age_hours)
        result = await self.session.execute(
            delete(StaffSessionModel).where(
                StaffSessionModel.revoked_at.isnot(None),
                StaffSessionModel.revoked_at < cutoff,
            )
        )
        return result.rowcount
