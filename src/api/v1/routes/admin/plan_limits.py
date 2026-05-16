"""Admin plan limits management.

URL: /api/v1/admin/plan-limits
Requires SUPER_ADMIN role.

Reads and writes plan limit overrides stored in the ``platform_config``
table under the ``plan_limits`` key. The response merges overrides on
top of the code-level defaults from ``plan.py`` so the admin always
sees the complete picture.

On PUT the new values are persisted to the DB AND hot-patched into the
in-memory ``PLAN_LIMITS`` dict so changes take effect immediately
without an API restart.
"""

import logging
from dataclasses import asdict
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.dependencies.auth import require_admin
from src.api.dependencies.database import get_db
from src.api.responses import SuccessResponse
from src.core.entities.plan import PLAN_LIMITS, PlanFeatures
from src.infrastructure.database.models.public.platform_config import (
    PlatformConfigModel,
)

logger = logging.getLogger(__name__)

router = APIRouter()

PLAN_LIMITS_KEY = "plan_limits"


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------


class PlanLimitsItem(BaseModel):
    key: str
    display_name: str
    # Limits
    max_products: int
    max_orders_per_month: int
    max_stores: int
    max_staff_members: int
    max_customers: int
    # Feature flags
    webhooks_enabled: bool
    custom_domain_enabled: bool
    api_access_enabled: bool
    analytics_enabled: bool
    discount_codes_enabled: bool
    # Pricing (piasters)
    monthly_price_piasters: int
    annual_price_piasters: int


class PlanLimitsResponse(BaseModel):
    plans: list[PlanLimitsItem]


class PlanLimitsUpdate(BaseModel):
    plans: list[PlanLimitsItem]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _plans_to_response() -> list[PlanLimitsItem]:
    """Convert the current in-memory PLAN_LIMITS to response items."""
    items = []
    for key, pf in PLAN_LIMITS.items():
        items.append(
            PlanLimitsItem(
                key=key,
                display_name=pf.display_name,
                max_products=pf.max_products,
                max_orders_per_month=pf.max_orders_per_month,
                max_stores=pf.max_stores,
                max_staff_members=pf.max_staff_members,
                max_customers=pf.max_customers,
                webhooks_enabled=pf.webhooks_enabled,
                custom_domain_enabled=pf.custom_domain_enabled,
                api_access_enabled=pf.api_access_enabled,
                analytics_enabled=pf.analytics_enabled,
                discount_codes_enabled=pf.discount_codes_enabled,
                monthly_price_piasters=pf.monthly_price_piasters,
                annual_price_piasters=pf.annual_price_piasters,
            )
        )
    return items


def _apply_overrides(overrides: dict[str, Any]) -> None:
    """Hot-patch PLAN_LIMITS in memory from a DB overrides dict."""
    for key, vals in overrides.items():
        if key not in PLAN_LIMITS or not isinstance(vals, dict):
            continue
        current = asdict(PLAN_LIMITS[key])
        current.update(vals)
        PLAN_LIMITS[key] = PlanFeatures(**current)


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@router.get(
    "",
    response_model=SuccessResponse[PlanLimitsResponse],
    summary="Get plan limits",
    operation_id="admin_get_plan_limits",
)
async def get_plan_limits(
    _admin_id: Annotated[UUID, Depends(require_admin)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """Return the current plan limits (code defaults merged with DB overrides)."""
    # Load overrides from DB and apply (in case they haven't been applied yet)
    result = await db.execute(
        select(PlatformConfigModel).where(
            PlatformConfigModel.key == PLAN_LIMITS_KEY
        )
    )
    row = result.scalar_one_or_none()
    if row and isinstance(row.value, dict):
        _apply_overrides(row.value)

    return SuccessResponse(
        data=PlanLimitsResponse(plans=_plans_to_response()),
        message="Plan limits retrieved",
    )


@router.put(
    "",
    response_model=SuccessResponse[PlanLimitsResponse],
    summary="Update plan limits",
    operation_id="admin_update_plan_limits",
)
async def update_plan_limits(
    request: PlanLimitsUpdate,
    _admin_id: Annotated[UUID, Depends(require_admin)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """Overwrite plan limits. Changes take effect immediately (hot-patched
    into memory) and persist across restarts (stored in DB).
    """
    # Build overrides dict keyed by plan name
    overrides: dict[str, dict[str, Any]] = {}
    for item in request.plans:
        overrides[item.key] = item.model_dump(exclude={"key"})

    # Upsert into platform_config
    stmt = (
        pg_insert(PlatformConfigModel)
        .values(
            key=PLAN_LIMITS_KEY,
            value=overrides,
            description="Plan feature limits and pricing (admin-managed)",
        )
        .on_conflict_do_update(
            index_elements=["key"],
            set_={"value": overrides},
        )
    )
    await db.execute(stmt)
    await db.commit()

    # Hot-patch in-memory
    _apply_overrides(overrides)

    logger.info("Plan limits updated by admin — plans=%s", list(overrides.keys()))

    return SuccessResponse(
        data=PlanLimitsResponse(plans=_plans_to_response()),
        message="Plan limits saved — changes are live immediately",
    )
