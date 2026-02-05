"""Skip onboarding step use case."""

from uuid import UUID

from src.core.entities.onboarding import NON_SKIPPABLE_STEPS, OnboardingStepKey, StoreOnboarding
from src.core.exceptions import AuthorizationError, EntityNotFoundError, ValidationError
from src.core.interfaces.repositories.onboarding_repository import (
    IOnboardingRepository,
)
from src.core.interfaces.repositories.store_repository import IStoreRepository


class SkipOnboardingStepUseCase:
    """Use case for skipping an onboarding step."""

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
        """Mark a step as skipped.

        Args:
            store_id: The store UUID.
            step_key: The onboarding step key string.
            user_id: The user UUID (for authorization).

        Returns:
            Updated StoreOnboarding entity.

        Raises:
            EntityNotFoundError: If store or onboarding not found.
            AuthorizationError: If user doesn't own the store.
            ValidationError: If step key is invalid or non-skippable.
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

        if step in NON_SKIPPABLE_STEPS:
            raise ValidationError(
                f"Step '{step_key}' cannot be skipped",
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

        onboarding.skip_step(step)
        return await self.onboarding_repository.update(onboarding)
