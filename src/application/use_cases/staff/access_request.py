"""Access request use cases."""

from dataclasses import dataclass
from datetime import datetime, timedelta
from uuid import UUID


@dataclass
class CreateAccessRequestInput:
    """Input for creating an access request."""

    tenant_id: UUID
    requester_user_id: UUID
    role_ids: list[UUID]
    permissions: list[str]
    justification: str | None = None
    expires_in_days: int = 7


@dataclass
class CreateAccessRequestOutput:
    """Output from creating an access request."""

    request_id: UUID
    status: str


class CreateAccessRequestUseCase:
    """Use case for creating an access request."""

    def __init__(self, db_session):
        self.db_session = db_session

    async def execute(
        self, input_data: CreateAccessRequestInput
    ) -> CreateAccessRequestOutput:
        """Execute the use case."""
        from uuid import uuid4

        from src.infrastructure.database.models.public.access_request import (
            AccessRequestModel,
            AccessRequestStatus,
        )

        expires_at = datetime.utcnow() + timedelta(days=input_data.expires_in_days)

        request = AccessRequestModel(
            id=uuid4(),
            tenant_id=input_data.tenant_id,
            requester_user_id=input_data.requester_user_id,
            requested_role_ids=input_data.role_ids,
            requested_permissions=input_data.permissions,
            justification=input_data.justification,
            status=AccessRequestStatus.PENDING,
            expires_at=expires_at,
        )
        self.db_session.add(request)
        await self.db_session.flush()
        await self.db_session.refresh(request)

        return CreateAccessRequestOutput(
            request_id=request.id,
            status="pending",
        )


async def _publish_access_request_created(event):
    """Publish access request created event."""
    from src.infrastructure.events.setup import get_event_bus

    event_bus = get_event_bus()
    await event_bus.publish(event)


@dataclass
class ReviewAccessRequestInput:
    """Input for reviewing an access request."""

    request_id: UUID
    reviewer_user_id: UUID
    approved: bool
    review_reason: str | None = None


@dataclass
class ReviewAccessRequestOutput:
    """Output from reviewing an access request."""

    status: str


class ReviewAccessRequestUseCase:
    """Use case for approving or denying an access request."""

    def __init__(self, db_session):
        self.db_session = db_session

    async def execute(
        self, input_data: ReviewAccessRequestInput
    ) -> ReviewAccessRequestOutput:
        """Execute the use case."""
        from sqlalchemy import update

        from src.infrastructure.database.models.public.access_request import (
            AccessRequestModel,
            AccessRequestStatus,
        )

        new_status = (
            AccessRequestStatus.APPROVED
            if input_data.approved
            else AccessRequestStatus.DENIED
        )

        result = await self.db_session.execute(
            update(AccessRequestModel)
            .where(
                AccessRequestModel.id == input_data.request_id,
                AccessRequestModel.status == AccessRequestStatus.PENDING,
            )
            .values(
                status=new_status,
                reviewer_user_id=input_data.reviewer_user_id,
                reviewed_at=datetime.utcnow(),
                review_reason=input_data.review_reason,
            )
        )
        await self.db_session.flush()

        return ReviewAccessRequestOutput(
            status=new_status.value if result.rowcount > 0 else "not_found"
        )
