"""Store onboarding routes.

Provides REST endpoints for merchant onboarding progress:
- GET  /{store_id}/onboarding          - Get onboarding progress
- POST /{store_id}/onboarding/complete/{step} - Complete a step
- POST /{store_id}/onboarding/skip/{step}     - Skip a step
- POST /{store_id}/onboarding/dismiss         - Dismiss onboarding
"""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Path

from src.api.dependencies import get_store_repository, require_store_owner
from src.api.dependencies.repositories import get_onboarding_repository
from src.api.responses import SuccessResponse
from src.api.v1.schemas.tenant.onboarding import (
    OnboardingResponse,
    OnboardingStepResponse,
)
from src.application.use_cases.onboarding import (
    CompleteOnboardingStepUseCase,
    DismissOnboardingUseCase,
    GetOnboardingUseCase,
    SkipOnboardingStepUseCase,
)
from src.core.entities.onboarding import (
    NON_SKIPPABLE_STEPS,
    OnboardingStepKey,
    StoreOnboarding,
)
from src.infrastructure.repositories import OnboardingRepository, StoreRepository

router = APIRouter(prefix="/{store_id}/onboarding")


def _build_onboarding_response(entity: StoreOnboarding) -> OnboardingResponse:
    """Build OnboardingResponse from domain entity."""
    entity._ensure_steps_initialized()

    step_responses = []
    for key in OnboardingStepKey:
        step_data = entity.steps.get(key.value, {})
        step_responses.append(
            OnboardingStepResponse(
                key=key.value,
                status=step_data.get("status", "pending"),
                is_skippable=key not in NON_SKIPPABLE_STEPS,
                completed_at=step_data.get("completed_at"),
                skipped_at=step_data.get("skipped_at"),
            )
        )

    return OnboardingResponse(
        id=str(entity.id),
        store_id=str(entity.store_id),
        steps=step_responses,
        completion_percentage=entity.completion_percentage,
        current_step=entity.current_step,
        is_completed=entity.is_completed,
        is_dismissed=entity.is_dismissed,
        completed_at=str(entity.completed_at) if entity.completed_at else None,
        created_at=str(entity.created_at),
        updated_at=str(entity.updated_at),
    )


@router.get(
    "",
    response_model=SuccessResponse[OnboardingResponse],
    summary="Get onboarding progress",
)
async def get_onboarding(
    store_id: Annotated[UUID, Path(description="Store ID")],
    user_id: Annotated[UUID, Depends(require_store_owner)],
    onboarding_repo: Annotated[
        OnboardingRepository, Depends(get_onboarding_repository)
    ],
    store_repo: Annotated[StoreRepository, Depends(get_store_repository)],
):
    """Get onboarding progress for a store.

    Returns the current onboarding state including all steps,
    completion percentage, and current step. Lazily initializes
    onboarding on first access with create_store already completed.
    """
    use_case = GetOnboardingUseCase(
        onboarding_repository=onboarding_repo,
        store_repository=store_repo,
    )

    result = await use_case.execute(store_id=store_id, user_id=user_id)

    return SuccessResponse(
        data=_build_onboarding_response(result),
        message="Onboarding progress retrieved successfully",
    )


@router.post(
    "/complete/{step}",
    response_model=SuccessResponse[OnboardingResponse],
    summary="Complete an onboarding step",
)
async def complete_step(
    store_id: Annotated[UUID, Path(description="Store ID")],
    step: Annotated[str, Path(description="Step key to complete")],
    user_id: Annotated[UUID, Depends(require_store_owner)],
    onboarding_repo: Annotated[
        OnboardingRepository, Depends(get_onboarding_repository)
    ],
    store_repo: Annotated[StoreRepository, Depends(get_store_repository)],
):
    """Mark an onboarding step as completed.

    Idempotent: completing an already-completed step is a no-op.
    When all steps are completed or skipped, the overall onboarding
    is automatically marked as complete.
    """
    use_case = CompleteOnboardingStepUseCase(
        onboarding_repository=onboarding_repo,
        store_repository=store_repo,
    )

    result = await use_case.execute(
        store_id=store_id,
        step_key=step,
        user_id=user_id,
    )

    return SuccessResponse(
        data=_build_onboarding_response(result),
        message=f"Step '{step}' completed successfully",
    )


@router.post(
    "/skip/{step}",
    response_model=SuccessResponse[OnboardingResponse],
    summary="Skip an onboarding step",
)
async def skip_step(
    store_id: Annotated[UUID, Path(description="Store ID")],
    step: Annotated[str, Path(description="Step key to skip")],
    user_id: Annotated[UUID, Depends(require_store_owner)],
    onboarding_repo: Annotated[
        OnboardingRepository, Depends(get_onboarding_repository)
    ],
    store_repo: Annotated[StoreRepository, Depends(get_store_repository)],
):
    """Skip an onboarding step.

    Skipped steps count toward completion percentage. Some steps
    (like create_store) cannot be skipped. Returns 422 for
    non-skippable steps.
    """
    use_case = SkipOnboardingStepUseCase(
        onboarding_repository=onboarding_repo,
        store_repository=store_repo,
    )

    result = await use_case.execute(
        store_id=store_id,
        step_key=step,
        user_id=user_id,
    )

    return SuccessResponse(
        data=_build_onboarding_response(result),
        message=f"Step '{step}' skipped successfully",
    )


@router.post(
    "/dismiss",
    response_model=SuccessResponse[OnboardingResponse],
    summary="Dismiss onboarding",
)
async def dismiss_onboarding(
    store_id: Annotated[UUID, Path(description="Store ID")],
    user_id: Annotated[UUID, Depends(require_store_owner)],
    onboarding_repo: Annotated[
        OnboardingRepository, Depends(get_onboarding_repository)
    ],
    store_repo: Annotated[StoreRepository, Depends(get_store_repository)],
):
    """Dismiss the onboarding entirely.

    The merchant can always revisit onboarding later. Dismissing
    does not complete any pending steps.
    """
    use_case = DismissOnboardingUseCase(
        onboarding_repository=onboarding_repo,
        store_repository=store_repo,
    )

    result = await use_case.execute(store_id=store_id, user_id=user_id)

    return SuccessResponse(
        data=_build_onboarding_response(result),
        message="Onboarding dismissed",
    )
