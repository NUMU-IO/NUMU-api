"""Helper for auto-completing onboarding steps from other use cases.

This module provides a fire-and-forget utility so that core use cases
(create_store, create_product, etc.) can mark onboarding steps as done
without coupling to the full onboarding use case layer.
"""

from uuid import UUID

from src.core.entities.onboarding import OnboardingStepKey, StoreOnboarding
from src.core.interfaces.repositories.onboarding_repository import (
    IOnboardingRepository,
)


async def try_complete_onboarding_step(
    onboarding_repository: IOnboardingRepository,
    store_id: UUID,
    step_key: OnboardingStepKey,
) -> None:
    """Best-effort onboarding step completion. Never raises.

    Looks up the onboarding record for *store_id*, marks *step_key*
    as completed, and persists the change.  If the record does not
    exist yet, it silently does nothing (the record will be lazily
    created on the first GET).

    This is meant to be called from other use cases/routes **after**
    their main operation succeeds.
    """
    try:
        onboarding = await onboarding_repository.get_by_store_id(store_id)
        if onboarding:
            changed = onboarding.complete_step(step_key)
            if changed:
                await onboarding_repository.update(onboarding)
    except Exception:
        pass  # Never block the main operation for onboarding tracking


async def init_onboarding_for_store(
    onboarding_repository: IOnboardingRepository,
    store_id: UUID,
) -> None:
    """Create the onboarding record for a newly created store.

    Initialises all steps and marks ``create_store`` as completed.
    Idempotent — does nothing if a record already exists.
    """
    try:
        existing = await onboarding_repository.get_by_store_id(store_id)
        if existing:
            return
        onboarding = StoreOnboarding(store_id=store_id)
        onboarding.complete_step(OnboardingStepKey.CREATE_STORE)
        await onboarding_repository.create(onboarding)
    except Exception:
        pass  # Never block store creation for onboarding
