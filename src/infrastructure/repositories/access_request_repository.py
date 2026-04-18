"""Access request repository implementation."""

from datetime import datetime
from uuid import UUID

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from src.infrastructure.database.models.public.access_request import (
    AccessRequestModel,
    AccessRequestStatus,
)


class AccessRequestRepository:
    """Repository for access requests."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get_by_id(self, request_id: UUID) -> AccessRequestModel | None:
        """Get access request by ID."""
        result = await self.session.execute(
            select(AccessRequestModel).where(AccessRequestModel.id == request_id)
        )
        return result.scalar_one_or_none()

    async def get_pending_by_tenant(self, tenant_id: UUID) -> list[AccessRequestModel]:
        """Get all pending access requests for a tenant."""
        result = await self.session.execute(
            select(AccessRequestModel).where(
                AccessRequestModel.tenant_id == tenant_id,
                AccessRequestModel.status == AccessRequestStatus.PENDING,
                or_(
                    AccessRequestModel.expires_at.is_(None),
                    AccessRequestModel.expires_at > datetime.utcnow(),
                ),
            )
        )
        return list(result.scalars().all())

    async def get_pending_by_requester(
        self, requester_user_id: UUID, tenant_id: UUID
    ) -> list[AccessRequestModel]:
        """Get pending requests by requester."""
        result = await self.session.execute(
            select(AccessRequestModel).where(
                AccessRequestModel.requester_user_id == requester_user_id,
                AccessRequestModel.tenant_id == tenant_id,
                AccessRequestModel.status == AccessRequestStatus.PENDING,
            )
        )
        return list(result.scalars().all())

    async def get_by_status(
        self, tenant_id: UUID, status: AccessRequestStatus
    ) -> list[AccessRequestModel]:
        """Get access requests by status."""
        result = await self.session.execute(
            select(AccessRequestModel).where(
                AccessRequestModel.tenant_id == tenant_id,
                AccessRequestModel.status == status,
            )
        )
        return list(result.scalars().all())

    async def create(self, request: AccessRequestModel) -> AccessRequestModel:
        """Create a new access request."""
        self.session.add(request)
        await self.session.flush()
        await self.session.refresh(request)
        return request

    async def update_status(
        self,
        request_id: UUID,
        status: AccessRequestStatus,
        reviewer_user_id: UUID | None = None,
        review_reason: str | None = None,
    ) -> bool:
        """Update access request status."""
        values = {"status": status}
        if reviewer_user_id:
            values["reviewer_user_id"] = reviewer_user_id
        if review_reason:
            values["review_reason"] = review_reason
        if status in (AccessRequestStatus.APPROVED, AccessRequestStatus.DENIED):
            values["reviewed_at"] = datetime.utcnow()

        await self.session.execute(
            update(AccessRequestModel)
            .where(AccessRequestModel.id == request_id)
            .values(**values)
        )
        await self.session.flush()
        return True

    async def expire_old_requests(self) -> int:
        """Expire old pending requests."""
        result = await self.session.execute(
            update(AccessRequestModel)
            .where(
                AccessRequestModel.status == AccessRequestStatus.PENDING,
                AccessRequestModel.expires_at < datetime.utcnow(),
            )
            .values(status=AccessRequestStatus.EXPIRED)
        )
        await self.session.flush()
        return result.rowcount


from sqlalchemy import or_
