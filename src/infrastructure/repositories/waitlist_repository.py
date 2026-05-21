"""Waitlist repository implementation."""

from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.entities.waitlist import WaitlistEntry, WaitlistStatus
from src.infrastructure.database.models.public.waitlist import WaitlistModel


class WaitlistRepository:
    """Waitlist repository using SQLAlchemy."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    def _to_entity(self, model: WaitlistModel) -> WaitlistEntry:
        return WaitlistEntry(
            id=model.id,
            email=model.email,
            name=model.name,
            company_name=model.company_name,
            phone=model.phone,
            status=model.status,
            priority_score=model.priority_score,
            referral_code=model.referral_code,
            referred_by=model.referred_by,
            referral_count=model.referral_count,
            invite_code=model.invite_code,
            invited_at=model.invited_at,
            converted_at=model.converted_at,
            source=model.source,
            notes=model.notes,
            created_at=model.created_at,
            updated_at=model.updated_at,
        )

    def _to_model(self, entity: WaitlistEntry) -> WaitlistModel:
        return WaitlistModel(
            id=entity.id,
            email=entity.email,
            name=entity.name,
            company_name=entity.company_name,
            phone=entity.phone,
            status=entity.status,
            priority_score=entity.priority_score,
            referral_code=entity.referral_code,
            referred_by=entity.referred_by,
            referral_count=entity.referral_count,
            invite_code=entity.invite_code,
            invited_at=entity.invited_at,
            converted_at=entity.converted_at,
            source=entity.source,
            notes=entity.notes,
        )

    async def get_by_id(self, entry_id: UUID) -> WaitlistEntry | None:
        result = await self.session.execute(
            select(WaitlistModel).where(WaitlistModel.id == entry_id)
        )
        model = result.scalar_one_or_none()
        return self._to_entity(model) if model else None

    async def get_by_email(self, email: str) -> WaitlistEntry | None:
        result = await self.session.execute(
            select(WaitlistModel).where(
                func.lower(WaitlistModel.email) == email.lower()
            )
        )
        model = result.scalar_one_or_none()
        return self._to_entity(model) if model else None

    async def get_by_invite_code(self, invite_code: str) -> WaitlistEntry | None:
        result = await self.session.execute(
            select(WaitlistModel).where(WaitlistModel.invite_code == invite_code)
        )
        model = result.scalar_one_or_none()
        return self._to_entity(model) if model else None

    async def get_by_referral_code(self, referral_code: str) -> WaitlistEntry | None:
        result = await self.session.execute(
            select(WaitlistModel).where(WaitlistModel.referral_code == referral_code)
        )
        model = result.scalar_one_or_none()
        return self._to_entity(model) if model else None

    async def email_exists(self, email: str) -> bool:
        result = await self.session.execute(
            select(WaitlistModel.id).where(
                func.lower(WaitlistModel.email) == email.lower()
            )
        )
        return result.scalar_one_or_none() is not None

    async def create(self, entity: WaitlistEntry) -> WaitlistEntry:
        model = self._to_model(entity)
        self.session.add(model)
        await self.session.flush()
        await self.session.refresh(model)
        return self._to_entity(model)

    async def update(self, entity: WaitlistEntry) -> WaitlistEntry:
        result = await self.session.execute(
            select(WaitlistModel).where(WaitlistModel.id == entity.id)
        )
        model = result.scalar_one_or_none()
        if not model:
            raise ValueError(f"Waitlist entry {entity.id} not found")

        model.email = entity.email
        model.name = entity.name
        model.company_name = entity.company_name
        model.phone = entity.phone
        model.status = entity.status
        model.priority_score = entity.priority_score
        model.referral_code = entity.referral_code
        model.referred_by = entity.referred_by
        model.referral_count = entity.referral_count
        model.invite_code = entity.invite_code
        model.invited_at = entity.invited_at
        model.converted_at = entity.converted_at
        model.source = entity.source
        model.notes = entity.notes

        await self.session.flush()
        await self.session.refresh(model)
        return self._to_entity(model)

    async def list_all(
        self,
        *,
        status: WaitlistStatus | None = None,
        skip: int = 0,
        limit: int = 50,
    ) -> list[WaitlistEntry]:
        query = select(WaitlistModel)

        if status is not None:
            query = query.where(WaitlistModel.status == status)

        query = (
            query.order_by(
                WaitlistModel.priority_score.desc(),
                WaitlistModel.created_at.asc(),
            )
            .offset(skip)
            .limit(limit)
        )

        result = await self.session.execute(query)
        return [self._to_entity(m) for m in result.scalars().all()]

    async def count(self, status: WaitlistStatus | None = None) -> int:
        query = select(func.count(WaitlistModel.id))
        if status is not None:
            query = query.where(WaitlistModel.status == status)
        result = await self.session.execute(query)
        return result.scalar() or 0

    async def increment_referral_count(self, entry_id: UUID) -> None:
        result = await self.session.execute(
            select(WaitlistModel).where(WaitlistModel.id == entry_id)
        )
        model = result.scalar_one_or_none()
        if model:
            model.referral_count += 1
            await self.session.flush()
