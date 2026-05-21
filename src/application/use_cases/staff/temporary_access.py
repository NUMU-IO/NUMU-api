"""Temporary access grant use case."""

from dataclasses import dataclass
from datetime import datetime, timedelta
from uuid import UUID


@dataclass
class GrantTemporaryAccessInput:
    """Input for granting temporary access."""

    membership_id: UUID
    role_ids: list[UUID]
    granted_by_id: UUID
    reason: str | None = None
    expires_in_hours: int = 24


@dataclass
class GrantTemporaryAccessOutput:
    """Output from granting temporary access."""

    grants: list[UUID]
    status: str


class GrantTemporaryAccessUseCase:
    """Use case for granting temporary role access."""

    def __init__(self, db_session):
        self.db_session = db_session

    async def execute(
        self, input_data: GrantTemporaryAccessInput
    ) -> GrantTemporaryAccessOutput:
        """Execute the use case."""
        from uuid import uuid4

        from src.infrastructure.database.models.public.temporary_access_grant import (
            TemporaryAccessGrantModel,
        )

        valid_from = datetime.utcnow()
        valid_until = datetime.utcnow() + timedelta(hours=input_data.expires_in_hours)
        grant_ids = []

        for role_id in input_data.role_ids:
            grant = TemporaryAccessGrantModel(
                id=uuid4(),
                membership_id=input_data.membership_id,
                role_id=role_id,
                granted_by_id=input_data.granted_by_id,
                reason=input_data.reason,
                valid_from=valid_from,
                valid_until=valid_until,
            )
            self.db_session.add(grant)
            grant_ids.append(grant.id)

        await self.db_session.flush()

        from src.core.events.staff_events import TemporaryAccessGrantedEvent
        from src.infrastructure.events.setup import get_event_bus

        event_bus = get_event_bus()
        await event_bus.publish(
            TemporaryAccessGrantedEvent(
                membership_id=str(input_data.membership_id),
                grant_id=str(grant_ids[0]) if grant_ids else "",
                permission_ids=[str(r) for r in input_data.role_ids],
                requester_user_id=str(input_data.granted_by_id),
                expires_at=valid_until,
            )
        )

        return GrantTemporaryAccessOutput(
            grants=grant_ids,
            status="granted",
        )


@dataclass
class RevokeTemporaryAccessInput:
    """Input for revoking temporary access."""

    grant_id: UUID
    revoked_by_id: UUID


@dataclass
class RevokeTemporaryAccessOutput:
    """Output from revoking temporary access."""

    status: str


class RevokeTemporaryAccessUseCase:
    """Use case for revoking temporary role access."""

    def __init__(self, db_session):
        self.db_session = db_session

    async def execute(
        self, input_data: RevokeTemporaryAccessInput
    ) -> RevokeTemporaryAccessOutput:
        """Execute the use case."""
        from sqlalchemy import update

        from src.infrastructure.database.models.public.temporary_access_grant import (
            TemporaryAccessGrantModel,
        )

        result = await self.db_session.execute(
            update(TemporaryAccessGrantModel)
            .where(
                TemporaryAccessGrantModel.id == input_data.grant_id,
                TemporaryAccessGrantModel.revoked_at.is_(None),
            )
            .values(revoked_at=datetime.utcnow())
        )
        await self.db_session.flush()

        if result.rowcount > 0:
            grant = await self.db_session.get(
                TemporaryAccessGrantModel, input_data.grant_id
            )
            from src.core.events.staff_events import TemporaryAccessRevokedEvent
            from src.infrastructure.events.setup import get_event_bus

            event_bus = get_event_bus()
            await event_bus.publish(
                TemporaryAccessRevokedEvent(
                    membership_id=str(grant.membership_id),
                    grant_id=str(input_data.grant_id),
                    permission_ids=[str(grant.role_id)],
                    revoked_by_id=str(input_data.revoked_by_id),
                )
            )

        return RevokeTemporaryAccessOutput(
            status="revoked" if result.rowcount > 0 else "not_found"
        )
