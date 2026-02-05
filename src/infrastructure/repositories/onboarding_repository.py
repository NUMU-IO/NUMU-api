"""Onboarding repository implementation."""

import copy
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.entities.onboarding import StoreOnboarding
from src.core.interfaces.repositories.onboarding_repository import (
    IOnboardingRepository,
)
from src.infrastructure.database.models.public.onboarding import (
    StoreOnboardingModel,
)


class OnboardingRepository(IOnboardingRepository):
    """Onboarding repository implementation using SQLAlchemy."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    def _to_entity(self, model: StoreOnboardingModel) -> StoreOnboarding:
        """Convert database model to domain entity."""
        return StoreOnboarding(
            id=model.id,
            store_id=model.store_id,
            steps=copy.deepcopy(model.steps) if model.steps else {},
            is_completed=model.is_completed,
            is_dismissed=model.is_dismissed,
            completed_at=model.completed_at,
            dismissed_at=model.dismissed_at,
            created_at=model.created_at,
            updated_at=model.updated_at,
        )

    def _to_model(self, entity: StoreOnboarding) -> StoreOnboardingModel:
        """Convert domain entity to database model."""
        return StoreOnboardingModel(
            id=entity.id,
            store_id=entity.store_id,
            steps=entity.steps,
            is_completed=entity.is_completed,
            is_dismissed=entity.is_dismissed,
            completed_at=entity.completed_at,
            dismissed_at=entity.dismissed_at,
            created_at=entity.created_at,
            updated_at=entity.updated_at,
        )

    async def get_by_id(self, entity_id: UUID) -> StoreOnboarding | None:
        """Get onboarding by ID."""
        result = await self.session.execute(
            select(StoreOnboardingModel).where(
                StoreOnboardingModel.id == entity_id
            )
        )
        model = result.scalar_one_or_none()
        return self._to_entity(model) if model else None

    async def get_by_store_id(self, store_id: UUID) -> StoreOnboarding | None:
        """Get onboarding progress for a store."""
        result = await self.session.execute(
            select(StoreOnboardingModel).where(
                StoreOnboardingModel.store_id == store_id
            )
        )
        model = result.scalar_one_or_none()
        return self._to_entity(model) if model else None

    async def get_all(
        self,
        skip: int = 0,
        limit: int = 100,
    ) -> list[StoreOnboarding]:
        """Get all onboarding records with pagination."""
        result = await self.session.execute(
            select(StoreOnboardingModel).offset(skip).limit(limit)
        )
        return [self._to_entity(model) for model in result.scalars().all()]

    async def create(self, entity: StoreOnboarding) -> StoreOnboarding:
        """Create a new onboarding record."""
        model = self._to_model(entity)
        self.session.add(model)
        await self.session.flush()
        await self.session.refresh(model)
        return self._to_entity(model)

    async def update(self, entity: StoreOnboarding) -> StoreOnboarding:
        """Update an existing onboarding record."""
        result = await self.session.execute(
            select(StoreOnboardingModel).where(
                StoreOnboardingModel.id == entity.id
            )
        )
        model = result.scalar_one_or_none()
        if model:
            model.steps = entity.steps
            model.is_completed = entity.is_completed
            model.is_dismissed = entity.is_dismissed
            model.completed_at = entity.completed_at
            model.dismissed_at = entity.dismissed_at
            await self.session.flush()
            await self.session.refresh(model)
            return self._to_entity(model)
        raise ValueError(f"Onboarding with id {entity.id} not found")

    async def upsert(self, entity: StoreOnboarding) -> StoreOnboarding:
        """Create or update onboarding record (idempotent)."""
        existing = await self.get_by_store_id(entity.store_id)
        if existing:
            entity.id = existing.id
            return await self.update(entity)
        return await self.create(entity)

    async def delete(self, entity_id: UUID) -> bool:
        """Delete an onboarding record by ID."""
        result = await self.session.execute(
            select(StoreOnboardingModel).where(
                StoreOnboardingModel.id == entity_id
            )
        )
        model = result.scalar_one_or_none()
        if model:
            await self.session.delete(model)
            await self.session.flush()
            return True
        return False

    async def count(self) -> int:
        """Get total count of onboarding records."""
        from sqlalchemy import func

        result = await self.session.execute(
            select(func.count(StoreOnboardingModel.id))
        )
        return result.scalar() or 0
