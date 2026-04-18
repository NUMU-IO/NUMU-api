"""Staff invitation repository implementation."""

from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from src.infrastructure.database.models.public.staff_invitation import (
    StaffInvitationModel,
)


class InvitationRepository:
    """Repository for staff invitations."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get_by_id(self, invitation_id: UUID) -> StaffInvitationModel | None:
        """Get invitation by ID."""
        result = await self.session.execute(
            select(StaffInvitationModel).where(StaffInvitationModel.id == invitation_id)
        )
        return result.scalar_one_or_none()

    async def get_by_token_hash(self, token_hash: str) -> StaffInvitationModel | None:
        """Get invitation by token hash."""
        result = await self.session.execute(
            select(StaffInvitationModel).where(
                StaffInvitationModel.token_hash == token_hash,
                StaffInvitationModel.accepted_at.is_(None),
                StaffInvitationModel.revoked_at.is_(None),
                StaffInvitationModel.expires_at > datetime.now(UTC),
            )
        )
        return result.scalar_one_or_none()

    async def get_by_email(
        self, email: str, tenant_id: UUID
    ) -> StaffInvitationModel | None:
        """Get pending invitation by email."""
        result = await self.session.execute(
            select(StaffInvitationModel).where(
                StaffInvitationModel.email == email.lower(),
                StaffInvitationModel.tenant_id == tenant_id,
                StaffInvitationModel.accepted_at.is_(None),
                StaffInvitationModel.revoked_at.is_(None),
            )
        )
        return result.scalar_one_or_none()

    async def get_pending_by_tenant(
        self, tenant_id: UUID
    ) -> list[StaffInvitationModel]:
        """Get all pending invitations for a tenant."""
        result = await self.session.execute(
            select(StaffInvitationModel).where(
                StaffInvitationModel.tenant_id == tenant_id,
                StaffInvitationModel.accepted_at.is_(None),
                StaffInvitationModel.revoked_at.is_(None),
            )
        )
        return list(result.scalars().all())

    async def create(self, invitation: StaffInvitationModel) -> StaffInvitationModel:
        """Create a new invitation."""
        self.session.add(invitation)
        await self.session.flush()
        await self.session.refresh(invitation)
        return invitation

    async def accept(self, invitation_id: UUID) -> bool:
        """Mark invitation as accepted."""
        await self.session.execute(
            update(StaffInvitationModel)
            .where(StaffInvitationModel.id == invitation_id)
            .values(accepted_at=datetime.now(UTC))
        )
        await self.session.flush()
        return True

    async def revoke(self, invitation_id: UUID) -> bool:
        """Revoke an invitation."""
        await self.session.execute(
            update(StaffInvitationModel)
            .where(StaffInvitationModel.id == invitation_id)
            .values(revoked_at=datetime.now(UTC))
        )
        await self.session.flush()
        return True

    async def increment_resend(self, invitation_id: UUID) -> bool:
        """Increment resend count."""
        await self.session.execute(
            update(StaffInvitationModel)
            .where(StaffInvitationModel.id == invitation_id)
            .values(resent_count=StaffInvitationModel.resent_count + 1)
        )
        await self.session.flush()
        return True

    async def rotate_token(self, invitation_id: UUID, new_token_hash: str) -> bool:
        """Rotate an invitation's token_hash and bump resent_count atomically.

        Uses a single Core UPDATE so the write isn't dependent on ORM
        autoflush ordering (the session is configured with autoflush=False).
        """
        await self.session.execute(
            update(StaffInvitationModel)
            .where(StaffInvitationModel.id == invitation_id)
            .values(
                token_hash=new_token_hash,
                resent_count=StaffInvitationModel.resent_count + 1,
            )
        )
        await self.session.flush()
        return True

    async def delete(self, invitation_id: UUID) -> bool:
        """Delete an invitation."""
        await self.session.execute(
            delete(StaffInvitationModel).where(StaffInvitationModel.id == invitation_id)
        )
        await self.session.flush()
        return True


from sqlalchemy import delete
