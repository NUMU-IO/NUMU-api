"""Billing-sync endpoints — backend-001.

Receives subscription state pushes from the Shopify-app's
syncSubscriptionToNumu helper, persists them, and exposes a read API
for the Numu dashboard + admin tools.

Auth: existing verify_internal_key dependency.
"""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Path

from src.api.dependencies.shopify import (
    get_shopify_subscription_repo,
    verify_internal_key,
)
from src.api.responses import SuccessResponse
from src.api.v1.schemas.shopify import (
    BillingSubscriptionResponse,
    BillingSyncRequest,
)
from src.application.use_cases.shopify.billing_sync import (
    BillingSyncUseCase,
    GetActiveSubscriptionUseCase,
)
from src.core.entities.shopify import ShopifySubscription
from src.infrastructure.repositories.shopify_repository import (
    ShopifySubscriptionRepository,
)

router = APIRouter(dependencies=[Depends(verify_internal_key)])


def _to_response(entity: ShopifySubscription) -> BillingSubscriptionResponse:
    """Domain entity → API response.

    Pydantic re-validates `status` and `plan_id` against the literal
    types declared in BillingSubscriptionResponse, so passing the entity's
    raw `str` fields here is safe — invalid values would surface as a
    422 at the boundary, not a silent type error.
    """
    return BillingSubscriptionResponse.model_validate({
        "store_id": str(entity.store_id),
        "shopify_subscription_id": entity.shopify_subscription_id,
        "status": entity.status,
        "plan_id": entity.plan_id,
        "is_trial": entity.is_trial,
        "trial_ends_at": entity.trial_ends_at,
        "current_period_end": entity.current_period_end,
        "cancelled_at": entity.cancelled_at,
        "synced_at": entity.synced_at,
    })


@router.post(
    "/{store_id}/billing/sync",
    response_model=SuccessResponse[BillingSubscriptionResponse],
    summary="Sync a Shopify subscription state from the Shopify app",
    operation_id="shopify_billing_sync",
)
async def sync_subscription(
    store_id: Annotated[UUID, Path()],
    body: BillingSyncRequest,
    repo: Annotated[
        ShopifySubscriptionRepository, Depends(get_shopify_subscription_repo)
    ],
) -> SuccessResponse[BillingSubscriptionResponse]:
    """Idempotent upsert keyed by (store_id, subscription_id).

    Called from the Shopify-app's `syncSubscriptionToNumu` after every
    `appSubscriptionCreate` / cancel / upgrade. Failures bubble back to
    the Shopify app, which logs but does not retry — the next merchant
    interaction will trigger another sync.
    """
    use_case = BillingSyncUseCase(repo)
    entity = await use_case.execute(
        store_id=store_id,
        shopify_subscription_id=body.subscription_id,
        status=body.status,
        plan_id=body.plan_id,
        is_trial=body.is_trial,
        trial_ends_at=body.trial_ends_at,
        current_period_end=body.current_period_end,
    )
    return SuccessResponse(
        data=_to_response(entity),
        message="Subscription synced",
    )


@router.get(
    "/{store_id}/billing/subscription",
    response_model=SuccessResponse[BillingSubscriptionResponse | None],
    summary="Get the merchant's active Shopify subscription (if any)",
    operation_id="shopify_billing_get_active",
)
async def get_active_subscription(
    store_id: Annotated[UUID, Path()],
    repo: Annotated[
        ShopifySubscriptionRepository, Depends(get_shopify_subscription_repo)
    ],
) -> SuccessResponse[BillingSubscriptionResponse | None]:
    """Return the merchant's most recent non-terminal subscription.

    Returns 200 with `data: null` when the store has no active
    subscription — callers should treat that as "no plan yet" rather
    than an error. 404 is reserved for unknown store IDs (not used here
    since the store_id is unverified at this layer; out-of-band stores
    return null).
    """
    use_case = GetActiveSubscriptionUseCase(repo)
    entity = await use_case.execute(store_id=store_id)
    if entity is None:
        return SuccessResponse(data=None, message="No active subscription")
    return SuccessResponse(data=_to_response(entity))
