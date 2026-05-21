"""Dismiss onboarding use case."""

from uuid import UUID

from src.core.entities.onboarding import StoreOnboarding
from src.core.exceptions import AuthorizationError, EntityNotFoundError
from src.core.interfaces.repositories.onboarding_repository import (
    IOnboardingRepository,
)
from src.core.interfaces.repositories.store_repository import IStoreRepository


class DismissOnboardingUseCase:
    """Use case for dismissing the onboarding entirely."""

    def __init__(
        self,
        onboarding_repository: IOnboardingRepository,
        store_repository: IStoreRepository,
    ) -> None:
        self.onboarding_repository = onboarding_repository
        self.store_repository = store_repository

    async def execute(
        self,
        store_id: UUID,
        user_id: UUID,
    ) -> StoreOnboarding:
        """Dismiss the onboarding for a store.

        Args:
            store_id: The store UUID.
            user_id: The user UUID (for authorization).

        Returns:
            Updated StoreOnboarding entity.

        Raises:
            EntityNotFoundError: If store or onboarding not found.
            AuthorizationError: If user doesn't own the store.
        """
        store = await self.store_repository.get_by_id(store_id)
        if not store:
            raise EntityNotFoundError("Store", str(store_id))

        if store.owner_id != user_id:
            raise AuthorizationError(
                "You don't have permission to update this store's onboarding"
            )

        onboarding = await self.onboarding_repository.get_by_store_id(store_id)
        if not onboarding:
            raise EntityNotFoundError("Onboarding", str(store_id))

        onboarding.dismiss()
        return await self.onboarding_repository.update(onboarding)
