"""Complete onboarding step use case."""

from uuid import UUID

from src.core.entities.onboarding import OnboardingStepKey, StoreOnboarding
from src.core.exceptions import AuthorizationError, EntityNotFoundError, ValidationError
from src.core.interfaces.repositories.onboarding_repository import (
    IOnboardingRepository,
)
from src.core.interfaces.repositories.store_repository import IStoreRepository


class CompleteOnboardingStepUseCase:
    """Use case for completing an onboarding step."""

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
        step_key: str,
        user_id: UUID,
    ) -> StoreOnboarding:
        """Mark a step as completed.

        Args:
            store_id: The store UUID.
            step_key: The onboarding step key string.
            user_id: The user UUID (for authorization).

        Returns:
            Updated StoreOnboarding entity.

        Raises:
            EntityNotFoundError: If store or onboarding not found.
            AuthorizationError: If user doesn't own the store.
            ValidationError: If step key is invalid.
        """
        # Validate step key
        try:
            step = OnboardingStepKey(step_key)
        except ValueError:
            raise ValidationError(
                f"Invalid onboarding step: '{step_key}'. "
                f"Valid steps: {', '.join(s.value for s in OnboardingStepKey)}",
                field="step_key",
            )

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

        onboarding.complete_step(step)
        return await self.onboarding_repository.update(onboarding)
