"""Plan and usage routes.

URL: /stores/{store_id}/plan

Legacy shapes kept for API callers: limits and switches now come from the
entitlement catalog, and ``/stores/{store_id}/entitlements`` is the new home.
"""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.dependencies import verify_store_ownership
from src.api.dependencies.database import get_db
from src.api.responses import SuccessResponse
from src.application.services.entitlement_service import (
    EntitlementService,
    plan_grants,
)
from src.core.entities.plan import PLAN_LIMITS, get_plan_features
from src.core.entities.store import Store
from src.core.entitlements import UNLIMITED
from src.infrastructure.database.models.public.tenant import TenantModel

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
    tenant = (
        await session.get(TenantModel, store.tenant_id) if store.tenant_id else None
    )
    if tenant is None:
        return SuccessResponse(
            data={"plan": "free", "error": "tenant not linked"},
            message="Usage unavailable",
        )

    ents = EntitlementService(session)
    legacy = get_plan_features(tenant.plan)

    async def meter(key: str) -> dict:
        usage = await ents.usage(tenant, key)
        unlimited = usage["limit"] == UNLIMITED
        return {
            "used": usage["used"],
            "limit": -1 if unlimited else usage["limit"],
            "unlimited": unlimited,
        }

    return SuccessResponse(
        data={
            "plan": tenant.plan,
            "display_name": legacy.display_name,
            "products": await meter("products"),
            "orders_this_month": await meter("orders_per_month"),
            "features": {
                "webhooks": legacy.webhooks_enabled,
                "custom_domain": await ents.has(tenant, "custom_domain"),
                "api_access": await ents.has(tenant, "api_access"),
                "analytics": legacy.analytics_enabled,
                "discount_codes": await ents.has(tenant, "discount_codes"),
            },
        },
        message="Usage retrieved",
    )


@router.get(
    "/limits",
    response_model=SuccessResponse[dict],
    summary="Get all plan limits",
    operation_id="get_all_plan_limits",
)
async def get_all_plan_limits(
    session: Annotated[AsyncSession, Depends(get_db)],
) -> SuccessResponse[dict]:
    """Return the feature matrix for all available plans."""
    grants = await plan_grants(session)
    matrix = {}
    for plan_name, features in PLAN_LIMITS.items():
        if plan_name == "developer":
            continue  # a partner's dev store, never a plan a merchant picks
        plan = grants.get(plan_name, {})

        def cap(key: str, plan: dict = plan) -> int | None:
            value = plan.get(key)
            return None if value == UNLIMITED else value

        matrix[plan_name] = {
            "display_name": features.display_name,
            "max_products": cap("products"),
            "max_orders_per_month": cap("orders_per_month"),
            "max_stores": cap("stores"),
            "max_staff_members": cap("staff_accounts"),
            "max_customers": features.max_customers
            if features.max_customers != -1
            else None,
            "webhooks_enabled": features.webhooks_enabled,
            "custom_domain_enabled": plan.get("custom_domain") is True,
            "api_access_enabled": plan.get("api_access") is True,
            "analytics_enabled": features.analytics_enabled,
            "discount_codes_enabled": plan.get("discount_codes") is True,
        }
    return SuccessResponse(data=matrix, message="Plan limits retrieved")
