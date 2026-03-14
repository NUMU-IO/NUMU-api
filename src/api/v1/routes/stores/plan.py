"""Plan and usage routes.

URL: /stores/{store_id}/plan
"""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.dependencies import verify_store_ownership
from src.api.dependencies.database import get_db
from src.api.responses import SuccessResponse
from src.application.services.plan_limit_service import PlanLimitService
from src.core.entities.plan import PLAN_LIMITS
from src.core.entities.store import Store

router = APIRouter(prefix="/{store_id}/plan")


@router.get(
    "/usage",
    response_model=SuccessResponse[dict],
    summary="Get plan usage",
    operation_id="get_plan_usage",
)
async def get_plan_usage(
    store_id: UUID,
    store: Annotated[Store, Depends(verify_store_ownership)],
    session: Annotated[AsyncSession, Depends(get_db)],
) -> SuccessResponse[dict]:
    """Return current resource usage vs plan limits for this store."""
    tenant_id = store.tenant_id
    if not tenant_id:
        return SuccessResponse(
            data={"plan": "free", "error": "tenant not linked"},
            message="Usage unavailable",
        )

    service = PlanLimitService(session)
    usage = await service.get_usage_summary(store_id, tenant_id)
    return SuccessResponse(data=usage, message="Usage retrieved")


@router.get(
    "/limits",
    response_model=SuccessResponse[dict],
    summary="Get all plan limits",
    operation_id="get_all_plan_limits",
)
async def get_all_plan_limits() -> SuccessResponse[dict]:
    """Return the feature matrix for all available plans."""
    matrix = {}
    for plan_name, features in PLAN_LIMITS.items():
        matrix[plan_name] = {
            "display_name": features.display_name,
            "max_products": features.max_products
            if features.max_products != -1
            else None,
            "max_orders_per_month": features.max_orders_per_month
            if features.max_orders_per_month != -1
            else None,
            "max_stores": features.max_stores if features.max_stores != -1 else None,
            "max_staff_members": features.max_staff_members
            if features.max_staff_members != -1
            else None,
            "max_customers": features.max_customers
            if features.max_customers != -1
            else None,
            "webhooks_enabled": features.webhooks_enabled,
            "custom_domain_enabled": features.custom_domain_enabled,
            "api_access_enabled": features.api_access_enabled,
            "analytics_enabled": features.analytics_enabled,
            "discount_codes_enabled": features.discount_codes_enabled,
        }
    return SuccessResponse(data=matrix, message="Plan limits retrieved")
