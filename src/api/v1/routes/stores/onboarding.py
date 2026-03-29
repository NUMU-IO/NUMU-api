"""Store onboarding routes.

Provides REST endpoints for merchant onboarding progress:
- GET  /{store_id}/onboarding          - Get onboarding progress
- POST /{store_id}/onboarding/complete/{step} - Complete a step
- POST /{store_id}/onboarding/skip/{step}     - Skip a step
- POST /{store_id}/onboarding/dismiss         - Dismiss onboarding
- POST /{store_id}/onboarding/configure       - Auto-configure from wizard
"""

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Path
from pydantic import BaseModel, Field

from src.api.dependencies import get_store_repository, verify_store_ownership
from src.api.dependencies.repositories import get_onboarding_repository
from src.api.responses import SuccessResponse
from src.api.v1.routes.stores.niche_templates import COUNTRY_DEFAULTS, NICHE_TEMPLATES
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
from src.application.use_cases.onboarding.auto_complete import (
    try_complete_onboarding_step,
)
from src.core.entities.onboarding import (
    NON_SKIPPABLE_STEPS,
    OnboardingStepKey,
    StoreOnboarding,
)
from src.core.entities.store import Store
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
    operation_id="get_onboarding",
)
async def get_onboarding(
    store: Annotated[Store, Depends(verify_store_ownership)],
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

    result = await use_case.execute(store_id=store.id, user_id=store.owner_id)

    return SuccessResponse(
        data=_build_onboarding_response(result),
        message="Onboarding progress retrieved successfully",
    )


@router.post(
    "/complete/{step}",
    response_model=SuccessResponse[OnboardingResponse],
    summary="Complete an onboarding step",
    operation_id="complete_step",
)
async def complete_step(
    store: Annotated[Store, Depends(verify_store_ownership)],
    step: Annotated[str, Path(description="Step key to complete")],
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
        store_id=store.id,
        step_key=step,
        user_id=store.owner_id,
    )

    return SuccessResponse(
        data=_build_onboarding_response(result),
        message=f"Step '{step}' completed successfully",
    )


@router.post(
    "/skip/{step}",
    response_model=SuccessResponse[OnboardingResponse],
    summary="Skip an onboarding step",
    operation_id="skip_step",
)
async def skip_step(
    store: Annotated[Store, Depends(verify_store_ownership)],
    step: Annotated[str, Path(description="Step key to skip")],
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
        store_id=store.id,
        step_key=step,
        user_id=store.owner_id,
    )

    return SuccessResponse(
        data=_build_onboarding_response(result),
        message=f"Step '{step}' skipped successfully",
    )


@router.post(
    "/dismiss",
    response_model=SuccessResponse[OnboardingResponse],
    summary="Dismiss onboarding",
    operation_id="dismiss_onboarding",
)
async def dismiss_onboarding(
    store: Annotated[Store, Depends(verify_store_ownership)],
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

    result = await use_case.execute(store_id=store.id, user_id=store.owner_id)

    return SuccessResponse(
        data=_build_onboarding_response(result),
        message="Onboarding dismissed",
    )


# ============ Smart Wizard Auto-Configure ============


class WizardConfigRequest(BaseModel):
    """Smart onboarding wizard configuration."""

    business_type: str = Field(
        ...,
        description="fashion, electronics, beauty, home, food, accessories, other",
    )
    country: str = Field(default="EG", max_length=2)
    shipping_preference: str = Field(..., description="bosta, manual, both")
    payment_methods: list[str] = Field(..., description="e.g. ['cod', 'paymob_card']")
    store_language: str = Field(default="ar", description="ar or en")


class WizardConfigResponse(BaseModel):
    """Result of auto-configuration."""

    configured: bool
    settings_applied: list[str]


@router.post(
    "/configure",
    response_model=SuccessResponse[WizardConfigResponse],
    summary="Auto-configure store from wizard answers",
    operation_id="configure_from_wizard",
)
async def configure_from_wizard(
    request: WizardConfigRequest,
    store: Annotated[Store, Depends(verify_store_ownership)],
    store_repo: Annotated[StoreRepository, Depends(get_store_repository)],
    onboarding_repo: Annotated[
        OnboardingRepository, Depends(get_onboarding_repository)
    ],
):
    """Auto-configure store based on onboarding wizard answers.

    Sets currency, payment methods, shipping zones, language,
    and theme based on the merchant's business type and location.
    """
    settings_applied: list[str] = []
    settings = store.settings or {}

    # ── 1. Currency from country ──
    country_data = COUNTRY_DEFAULTS.get(request.country, COUNTRY_DEFAULTS["EG"])
    store.default_currency = country_data["currency"]
    settings_applied.append(f"currency:{country_data['currency']}")

    # ── 2. Language ──
    store.default_language = request.store_language
    settings_applied.append(f"language:{request.store_language}")

    # ── 3. Payment methods ──
    payment_settings = settings.get(
        "payment",
        {
            "cod": {"enabled": True, "is_configured": True, "last_configured": None},
            "fawry": {
                "enabled": False,
                "is_configured": False,
                "last_configured": None,
            },
            "paymob": {
                "enabled": False,
                "is_configured": False,
                "last_configured": None,
            },
            "vodafone_cash": {
                "enabled": False,
                "is_configured": False,
                "last_configured": None,
            },
            "bank_transfer": {
                "enabled": False,
                "is_configured": False,
                "last_configured": None,
            },
            "bank_accounts_count": 0,
        },
    )

    # COD is always enabled
    payment_settings["cod"] = {
        "enabled": True,
        "is_configured": True,
        "last_configured": None,
    }
    settings_applied.append("payment:cod")

    for method in request.payment_methods:
        if method == "cod":
            continue
        # Map wizard names to settings keys
        method_map = {
            "paymob_card": "paymob",
            "paymob_wallet": "paymob",
            "fawry": "fawry",
            "kashier": "bank_transfer",
        }
        settings_key = method_map.get(method, method)
        if settings_key in payment_settings and isinstance(
            payment_settings[settings_key], dict
        ):
            payment_settings[settings_key]["enabled"] = True
            settings_applied.append(f"payment:{method}")

    settings["payment"] = payment_settings

    # ── 4. Shipping ──
    shipping_settings = settings.get(
        "shipping",
        {
            "aramex": {
                "enabled": False,
                "is_configured": False,
                "last_configured": None,
            },
            "bosta": {
                "enabled": False,
                "is_configured": False,
                "last_configured": None,
            },
            "mylerz": {
                "enabled": False,
                "is_configured": False,
                "last_configured": None,
            },
            "manual": {"enabled": True, "is_configured": True, "last_configured": None},
            "zones": [],
            "free_shipping_threshold": 500,
        },
    )

    if request.shipping_preference in ("bosta", "both"):
        shipping_settings["bosta"] = {
            "enabled": True,
            "is_configured": False,
            "last_configured": None,
        }
        settings_applied.append("shipping:bosta")

    if request.shipping_preference in ("manual", "both"):
        shipping_settings["manual"] = {
            "enabled": True,
            "is_configured": True,
            "last_configured": None,
        }
        # Create default zones for the country
        zones = []
        for zone_name in country_data.get("shipping_zones", []):
            zones.append({
                "id": str(uuid.uuid4()),
                "zone": zone_name,
                "governorates": zone_name,
                "rate": 50,
                "estimated_days": "2-4 days",
            })
        shipping_settings["zones"] = zones
        settings_applied.append("shipping:manual_zones")

    settings["shipping"] = shipping_settings

    # ── 5. Theme from business type ──
    niche = NICHE_TEMPLATES.get(request.business_type, NICHE_TEMPLATES["other"])
    theme_settings = store.theme_settings or {}
    theme_settings["theme"] = niche["theme"]
    theme_settings["suggested_categories"] = niche["suggested_categories"]
    theme_settings["suggested_sections"] = niche["suggested_sections"]
    store.theme_settings = theme_settings
    settings_applied.append(f"theme:{niche['theme']}")

    # ── 6. Persist ──
    store.settings = settings
    await store_repo.update(store)

    # ── 7. Auto-complete onboarding steps ──
    await try_complete_onboarding_step(
        onboarding_repo, store.id, OnboardingStepKey.CONFIGURE_PAYMENT
    )
    await try_complete_onboarding_step(
        onboarding_repo, store.id, OnboardingStepKey.ADD_SHIPPING
    )

    return SuccessResponse(
        data=WizardConfigResponse(
            configured=True,
            settings_applied=settings_applied,
        ),
        message="Store configured successfully from wizard",
    )
