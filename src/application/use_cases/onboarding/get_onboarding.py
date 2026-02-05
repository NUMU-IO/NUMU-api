"""Get onboarding progress use case."""

from uuid import UUID

from src.core.entities.onboarding import (
    OnboardingStepKey,
    StoreOnboarding,
)
from src.core.exceptions import AuthorizationError, EntityNotFoundError
from src.core.interfaces.repositories.onboarding_repository import (
    IOnboardingRepository,
)
from src.core.interfaces.repositories.store_repository import IStoreRepository


class GetOnboardingUseCase:
    """Use case for retrieving onboarding state.

    Lazily initializes onboarding on first access, with
    create_store already marked as completed.
    """

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
        """Get onboarding progress, creating it if it does not exist.

        Args:
            store_id: The store UUID.
            user_id: The user UUID (for authorization).

        Returns:
            StoreOnboarding entity with current progress.

        Raises:
            EntityNotFoundError: If store not found.
            AuthorizationError: If user doesn't own the store.
        """
        store = await self.store_repository.get_by_id(store_id)
        if not store:
            raise EntityNotFoundError("Store", str(store_id))

        if store.owner_id != user_id:
            raise AuthorizationError(
                "You don't have permission to view this store's onboarding"
            )

        onboarding = await self.onboarding_repository.get_by_store_id(store_id)

        if not onboarding:
            onboarding = StoreOnboarding(store_id=store_id)
            onboarding.complete_step(OnboardingStepKey.CREATE_STORE)
            onboarding = await self.onboarding_repository.create(onboarding)

        return onboarding
