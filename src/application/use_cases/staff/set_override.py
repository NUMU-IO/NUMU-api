"""Set override use case."""

from dataclasses import dataclass
from datetime import datetime
from uuid import UUID


@dataclass
class SetOverrideInput:
    """Input for setting a permission override."""

    membership_id: UUID
    permission_id: UUID
    effect: str
    granted_by_id: UUID | None = None
    reason: str | None = None
    expires_at: datetime | None = None


@dataclass
class SetOverrideOutput:
    """Output from setting an override."""

    override_id: UUID
    status: str


class SetOverrideUseCase:
    """Use case for setting a permission override."""

    def __init__(self, db_session):
        self.db_session = db_session

    async def execute(self, input_data: SetOverrideInput) -> SetOverrideOutput:
        """Execute the use case."""
        from uuid import uuid4

        from src.infrastructure.database.models.public.membership_override import (
            MembershipOverrideModel,
            OverrideEffect,
        )

        effect = OverrideEffect(input_data.effect)

        existing = await self.db_session.execute(
            select(MembershipOverrideModel).where(
                MembershipOverrideModel.membership_id == input_data.membership_id,
                MembershipOverrideModel.permission_id == input_data.permission_id,
            )
        ).scalar_one_or_none()

        if existing:
            existing.effect = effect
            existing.granted_by_id = input_data.granted_by_id
            existing.reason = input_data.reason
            existing.expires_at = input_data.expires_at
            existing.updated_at = datetime.utcnow()
            override_id = existing.id
        else:
            override = MembershipOverrideModel(
                id=uuid4(),
                membership_id=input_data.membership_id,
                permission_id=input_data.permission_id,
                effect=effect,
                granted_by_id=input_data.granted_by_id,
                reason=input_data.reason,
                expires_at=input_data.expires_at,
            )
            self.db_session.add(override)
            await self.db_session.flush()
            override_id = override.id

        from src.core.events.staff_events import PermissionOverrideSetEvent
        from src.infrastructure.events.setup import get_event_bus

        event_bus = get_event_bus()
        await event_bus.publish(
            PermissionOverrideSetEvent(
                membership_id=str(input_data.membership_id),
                permission_id=str(input_data.permission_id),
                effect=input_data.effect,
                granted_by_id=str(input_data.granted_by_id)
                if input_data.granted_by_id
                else None,
            )
        )

        return SetOverrideOutput(override_id=override_id, status="set")


@dataclass
class ClearOverrideInput:
    """Input for clearing an override."""

    membership_id: UUID
    permission_id: UUID


@dataclass
class ClearOverrideOutput:
    """Output from clearing an override."""

    status: str


class ClearOverrideUseCase:
    """Use case for clearing a permission override."""

    def __init__(self, db_session):
        self.db_session = db_session

    async def execute(self, input_data: ClearOverrideInput) -> ClearOverrideOutput:
        """Execute the use case."""
        from sqlalchemy import delete

        from src.infrastructure.database.models.public.membership_override import (
            MembershipOverrideModel,
        )

        result = await self.db_session.execute(
            delete(MembershipOverrideModel).where(
                MembershipOverrideModel.membership_id == input_data.membership_id,
                MembershipOverrideModel.permission_id == input_data.permission_id,
            )
        )
        await self.db_session.flush()

        if result.rowcount > 0:
            from src.core.events.staff_events import PermissionOverrideClearedEvent
            from src.infrastructure.events.setup import get_event_bus

            event_bus = get_event_bus()
            await event_bus.publish(
                PermissionOverrideClearedEvent(
                    membership_id=str(input_data.membership_id),
                    permission_id=str(input_data.permission_id),
                )
            )

        return ClearOverrideOutput(
            status="cleared" if result.rowcount > 0 else "not_found"
        )


from sqlalchemy import select
