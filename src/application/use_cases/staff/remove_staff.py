"""Remove staff use case."""

from dataclasses import dataclass
from uuid import UUID


@dataclass
class RemoveStaffInput:
    """Input for removing a staff member."""

    membership_id: UUID
    removed_by_id: UUID
    reason: str | None = None


@dataclass
class RemoveStaffOutput:
    """Output from removing a staff member."""

    status: str


class RemoveStaffUseCase:
    """Use case for removing a staff member from a tenant."""

    def __init__(self, db_session):
        self.db_session = db_session

    async def execute(self, input_data: RemoveStaffInput) -> RemoveStaffOutput:
        """Execute the use case."""
        from datetime import datetime

        from sqlalchemy import update

        from src.infrastructure.database.models.public.tenant_membership import (
            TenantMembershipModel,
        )

        result = await self.db_session.execute(
            update(TenantMembershipModel)
            .where(
                TenantMembershipModel.id == input_data.membership_id,
                TenantMembershipModel.is_owner.is_(False),
                TenantMembershipModel.deleted_at.is_(None),
            )
            .values(
                deleted_at=datetime.utcnow(),
            )
        )
        await self.db_session.flush()

        if result.rowcount == 0:
            return RemoveStaffOutput(status="not_found")

        from src.infrastructure.repositories.staff_session_repository import (
            StaffSessionRepository,
        )

        session_repo = StaffSessionRepository(self.db_session)
        await session_repo.revoke_by_membership(
            input_data.membership_id, input_data.removed_by_id
        )

        from src.core.events.staff_events import StaffRemovedEvent
        from src.infrastructure.events.setup import get_event_bus

        event_bus = get_event_bus()
        await event_bus.publish(
            StaffRemovedEvent(
                membership_id=str(input_data.membership_id),
                removed_by_id=str(input_data.removed_by_id),
                reason=input_data.reason,
            )
        )

        return RemoveStaffOutput(status="removed")
