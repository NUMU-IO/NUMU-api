"""Admin landing page + pricing configuration endpoints.

URL: /api/v1/admin/landing-config
     /api/v1/admin/landing-config/pricing-plans
Requires SUPER_ADMIN role.
"""

import logging
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm.attributes import flag_modified

from src.api.dependencies.auth import require_admin
from src.api.dependencies.database import get_db
from src.api.responses import SuccessResponse
from src.api.v1.routes.public.landing import DEFAULT_PRICING_PLANS
from src.infrastructure.database.models.public.platform_config import (
    DEFAULT_LANDING_CONFIG,
    PlatformConfigModel,
)

logger = logging.getLogger(__name__)

router = APIRouter()

LANDING_PAGE_KEY = "landing_page"


# ---------------------------------------------------------------------------
# Request / response schemas
# ---------------------------------------------------------------------------


class SectionConfig(BaseModel):
    visible: bool
    order: int


class UpdateLandingConfigRequest(BaseModel):
    sections: dict[str, SectionConfig]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _get_or_create_config(db: AsyncSession) -> PlatformConfigModel:
    """Get the landing page config row, creating it with defaults if absent.

    Race-safe via INSERT ... ON CONFLICT DO NOTHING + re-SELECT.
    """
    result = await db.execute(
        select(PlatformConfigModel).where(PlatformConfigModel.key == LANDING_PAGE_KEY)
    )
    config = result.scalar_one_or_none()

    if config is None:
        stmt = (
            pg_insert(PlatformConfigModel)
            .values(
                key=LANDING_PAGE_KEY,
                value=DEFAULT_LANDING_CONFIG,
                description="Landing page section visibility and ordering",
            )
            .on_conflict_do_nothing(index_elements=["key"])
        )
        await db.execute(stmt)
        await db.flush()
        result = await db.execute(
            select(PlatformConfigModel).where(
                PlatformConfigModel.key == LANDING_PAGE_KEY
            )
        )
        config = result.scalar_one()

    return config


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.get(
    "/",
    response_model=SuccessResponse[dict],
    summary="Get landing page config (admin)",
    operation_id="admin_get_landing_config",
)
async def get_landing_config(
    _admin_id: Annotated[UUID, Depends(require_admin)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """Return the current landing page section configuration."""
    config = await _get_or_create_config(db)
    return SuccessResponse(
        data=config.value,
        message="Landing page configuration",
    )


@router.put(
    "/",
    response_model=SuccessResponse[dict],
    summary="Update landing page config (admin)",
    operation_id="admin_update_landing_config",
)
async def update_landing_config(
    request: UpdateLandingConfigRequest,
    _admin_id: Annotated[UUID, Depends(require_admin)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """Update landing page section visibility and ordering."""
    config = await _get_or_create_config(db)

    # Merge incoming sections with existing config to preserve unknown keys
    current_sections = config.value.get("sections", {})
    for section_id, section_cfg in request.sections.items():
        current_sections[section_id] = {
            "visible": section_cfg.visible,
            "order": section_cfg.order,
        }

    # Reassign to trigger SQLAlchemy change detection on JSONB
    config.value = {**config.value, "sections": current_sections}
    flag_modified(config, "value")

    logger.info(f"Landing page config updated by admin {_admin_id}")

    return SuccessResponse(
        data=config.value,
        message="Landing page configuration updated",
    )


# ---------------------------------------------------------------------------
# Pricing plans CRUD
# ---------------------------------------------------------------------------

PRICING_KEY = "pricing_plans"


class PlanFeatureItem(BaseModel):
    en: str
    ar: str


class PlanItem(BaseModel):
    key: str
    name_en: str
    name_ar: str
    price_monthly: int  # EGP, -1 = custom
    price_annual: int
    currency: str = "EGP"
    cta: str  # try_demo, subscribe, contact
    popular: bool = False
    features: list[PlanFeatureItem]


class PromoConfig(BaseModel):
    code: str
    text_en: str
    text_ar: str


class UpdatePricingPlansRequest(BaseModel):
    plans: list[PlanItem]
    promo: PromoConfig | None = None


@router.get(
    "/pricing-plans",
    response_model=SuccessResponse[dict],
    summary="Get pricing plans config (admin)",
    operation_id="admin_get_pricing_plans",
)
async def admin_get_pricing_plans(
    _admin_id: Annotated[UUID, Depends(require_admin)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """Return current pricing plan configuration."""
    result = await db.execute(
        select(PlatformConfigModel).where(PlatformConfigModel.key == PRICING_KEY)
    )
    config = result.scalar_one_or_none()
    data = config.value if config else DEFAULT_PRICING_PLANS

    return SuccessResponse(data=data, message="Pricing plans configuration")


@router.put(
    "/pricing-plans",
    response_model=SuccessResponse[dict],
    summary="Update pricing plans (admin)",
    operation_id="admin_update_pricing_plans",
)
async def admin_update_pricing_plans(
    request: UpdatePricingPlansRequest,
    _admin_id: Annotated[UUID, Depends(require_admin)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """Update pricing plans displayed on the landing page.

    Changes take effect immediately — the public endpoint
    ``GET /public/pricing-plans`` reads from this config.
    """
    new_value = {
        "plans": [p.model_dump() for p in request.plans],
    }
    if request.promo:
        new_value["promo"] = request.promo.model_dump()

    # Race-safe upsert: previously this did SELECT → if-None INSERT else
    # mutate-and-flag_modified. Two admins saving simultaneously on a fresh
    # DB would both INSERT and trip platform_config_key_key.
    stmt = (
        pg_insert(PlatformConfigModel)
        .values(
            key=PRICING_KEY,
            value=new_value,
            description="Pricing plans for landing page (admin-editable)",
        )
        .on_conflict_do_update(
            index_elements=["key"],
            set_={"value": new_value},
        )
    )
    await db.execute(stmt)
    await db.flush()
    logger.info("Pricing plans updated by admin %s", _admin_id)

    return SuccessResponse(data=new_value, message="Pricing plans updated")


# ---------------------------------------------------------------------------
# Signup & trial settings (trial length/visibility, payg card visibility)
# ---------------------------------------------------------------------------


class SignupSettingsPatch(BaseModel):
    """Partial update; ``None`` clears an override back to the default."""

    trial_enabled: bool | None = None
    trial_days: int | None = Field(default=None, ge=1, le=365)
    trial_visible_on_landing: bool | None = None
    payg_visible_on_landing: bool | None = None


@router.get(
    "/signup",
    response_model=SuccessResponse[dict],
    summary="Get signup & trial settings (admin)",
    operation_id="admin_get_signup_settings",
)
async def admin_get_signup_settings(
    _admin_id: Annotated[UUID, Depends(require_admin)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """Effective signup/trial configuration (defaults + admin overrides)."""
    from src.application.services.signup_settings import (
        get_signup_settings,
        signup_settings_to_dict,
    )

    signup = await get_signup_settings(db, use_cache=False)
    return SuccessResponse(
        data=signup_settings_to_dict(signup), message="Signup settings"
    )


@router.put(
    "/signup",
    response_model=SuccessResponse[dict],
    summary="Update signup & trial settings (admin)",
    operation_id="admin_update_signup_settings",
)
async def admin_update_signup_settings(
    request: SignupSettingsPatch,
    _admin_id: Annotated[UUID, Depends(require_admin)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """Control the free trial (on/off, length in days, landing visibility)
    and whether the Pay-as-you-Grow card appears on the landing page.
    Live immediately — registration and ``GET /public/pricing-plans``
    both read this config."""
    from src.application.services.signup_settings import (
        signup_settings_to_dict,
        update_signup_settings,
    )

    merged = await update_signup_settings(db, request.model_dump(exclude_unset=True))
    await db.commit()
    logger.info("Signup settings updated by admin %s", _admin_id)
    return SuccessResponse(
        data=signup_settings_to_dict(merged), message="Signup settings updated"
    )
