"""Onboarding repository interface."""

from abc import abstractmethod
from uuid import UUID

from src.core.entities.onboarding import StoreOnboarding
from src.core.interfaces.repositories.base import BaseRepository


class IOnboardingRepository(BaseRepository[StoreOnboarding]):
    """Onboarding repository interface."""

    @abstractmethod
    async def get_by_store_id(self, store_id: UUID) -> StoreOnboarding | None:
        """Get onboarding progress for a store."""
        ...

    @abstractmethod
    async def upsert(self, entity: StoreOnboarding) -> StoreOnboarding:
        """Create or update onboarding record (idempotent)."""
        ...
