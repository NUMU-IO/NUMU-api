"""Public storefront routes for the offers-v2 / Promotions feature.

URL: /storefront/store/{store_id}/promotions/...

These routes are publicly accessible (no merchant auth):
* `GET .../active` — the hot read path the bazaar fetches once per
  page-load to know what to render.
* `POST .../{promotion_id}/events` — fire-and-forget analytics writes.
* `POST .../{promotion_id}/dismiss` — record a per-visitor / per-customer
  suppression so the same shopper isn't nagged twice.

The cart-side `apply coupon v2` flow lives in [coupon.py](./coupon.py) and
is tightly coupled to the cart code path; that integration is deferred
to step 12 of the offers-v2 plan (storefront checkout discounts).
"""

from datetime import UTC, datetime
from typing import Annotated, Any, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Path, Request, status
from pydantic import BaseModel, ConfigDict, Field

from src.api.dependencies.auth import get_optional_customer
from src.api.dependencies.feature_flags import require_feature_flag
from src.api.dependencies.repositories import (
    get_coupon_repository,
    get_promotion_dismissal_repository,
    get_promotion_display_repository,
    get_promotion_event_repository,
    get_promotion_repository,
    get_promotion_target_repository,
    get_store_repository,
)
from src.api.responses import SuccessResponse
from src.application.dto.promotion_resolution import (
    ActivePromotionsOutput,
    VisitorContextInput,
)
from src.application.use_cases.promotions.dismiss_promotion import (
    DismissPromotionUseCase,
)
from src.application.use_cases.promotions.record_promotion_event import (
    RecordPromotionEventUseCase,
)
from src.application.use_cases.promotions.resolve_active_promotions import (
    ResolveActivePromotionsUseCase,
)
from src.core.entities.customer import Customer
from src.core.enums.promotion_enums import PromotionEventType
from src.core.exceptions import EntityNotFoundError
from src.core.services.promotion_eligibility_checker import (
    PromotionEligibilityChecker,
)
from src.core.services.promotion_resolver import PromotionResolver
from src.infrastructure.repositories import StoreRepository
from src.infrastructure.repositories.promotion_dismissal_repository import (
    PromotionDismissalRepository,
)
from src.infrastructure.repositories.promotion_event_repository import (
    PromotionEventRepository,
)
from src.infrastructure.repositories.promotion_repository import (
    PromotionDisplayRepository,
    PromotionRepository,
    PromotionTargetRepository,
)

router = APIRouter(
    # Per the offers-v2 rollout plan (step 14 §2): the storefront's
    # `/promotions/*` endpoints 404 until the tenant has
    # `ff_storefront_promo_render` enabled. Returning 404 (not 403)
    # avoids signalling the feature exists during phased rollout.
    dependencies=[Depends(require_feature_flag("ff_storefront_promo_render"))],
)

_VISITOR_COOKIE = "numu_visitor"


def _visitor_token(request: Request) -> str | None:
    """Read the anonymous visitor cookie if present."""
    return request.cookies.get(_VISITOR_COOKIE)


# --------------------------------------------------------------------------- #
# GET /promotions/active                                                      #
# --------------------------------------------------------------------------- #


@router.get(
    "/promotions/active",
    response_model=SuccessResponse[ActivePromotionsOutput],
    summary="Active promotions for the current visitor, grouped by surface",
    operation_id="storefront_active_promotions",
)
async def get_active_promotions(
    request: Request,
    store_id: Annotated[UUID, Path()],
    promo_repo: Annotated[PromotionRepository, Depends(get_promotion_repository)],
    display_repo: Annotated[
        PromotionDisplayRepository, Depends(get_promotion_display_repository)
    ],
    target_repo: Annotated[
        PromotionTargetRepository, Depends(get_promotion_target_repository)
    ],
    dismissal_repo: Annotated[
        PromotionDismissalRepository,
        Depends(get_promotion_dismissal_repository),
    ],
    coupon_repo: Annotated[Any, Depends(get_coupon_repository)],
    store_repo: Annotated[StoreRepository, Depends(get_store_repository)],
    customer: Annotated[Customer | None, Depends(get_optional_customer)] = None,
    page: Annotated[str, str] = "/",
    device: Annotated[Literal["desktop", "mobile", "tablet"], str] = "desktop",
    locale: Annotated[Literal["en", "ar"], str] = "ar",
) -> SuccessResponse[ActivePromotionsOutput]:
    store = await store_repo.get_by_id(store_id)
    if store is None:
        raise EntityNotFoundError("Store", str(store_id))

    visitor = VisitorContextInput(
        customer_id=customer.id if customer else None,
        visitor_token=_visitor_token(request),
        is_logged_in=customer is not None,
        device=device,
        page_path=page,
        locale=locale,
    )

    resolver = PromotionResolver(
        promotion_repo=promo_repo,
        display_repo=display_repo,
        target_repo=target_repo,
        dismissal_repo=dismissal_repo,
        eligibility_checker=PromotionEligibilityChecker(),
    )
    use_case = ResolveActivePromotionsUseCase(
        resolver=resolver, coupon_repo=coupon_repo
    )
    out = await use_case.execute(
        store_id=store_id, tenant_id=store.tenant_id, visitor=visitor
    )
    return SuccessResponse(data=out)


# --------------------------------------------------------------------------- #
# POST /promotions/{promotion_id}/events                                      #
# --------------------------------------------------------------------------- #


class RecordEventRequest(BaseModel):
    """Customer-side analytics event payload.

    `redeem` and `convert` are intentionally NOT in the allowed set —
    those are written server-side at coupon-apply / order-paid time so
    the public API can't fake redemptions.
    """

    model_config = ConfigDict(extra="forbid")

    event_type: Literal["impression", "click", "dismiss"]
    metadata: dict[str, Any] = Field(default_factory=dict)


@router.post(
    "/promotions/{promotion_id}/events",
    status_code=status.HTTP_202_ACCEPTED,
    summary="Record a customer-side promotion event",
    operation_id="storefront_record_promotion_event",
)
async def record_event(
    request: Request,
    store_id: Annotated[UUID, Path()],
    promotion_id: Annotated[UUID, Path()],
    body: RecordEventRequest,
    promo_repo: Annotated[PromotionRepository, Depends(get_promotion_repository)],
    event_repo: Annotated[
        PromotionEventRepository, Depends(get_promotion_event_repository)
    ],
    store_repo: Annotated[StoreRepository, Depends(get_store_repository)],
    customer: Annotated[Customer | None, Depends(get_optional_customer)] = None,
) -> dict:
    store = await store_repo.get_by_id(store_id)
    if store is None:
        raise EntityNotFoundError("Store", str(store_id))

    use_case = RecordPromotionEventUseCase(
        promotion_repo=promo_repo, event_repo=event_repo
    )
    await use_case.execute(
        tenant_id=store.tenant_id,
        store_id=store_id,
        promotion_id=promotion_id,
        event_type=PromotionEventType(body.event_type),
        customer_id=customer.id if customer else None,
        session_id=_visitor_token(request),
        metadata=body.metadata,
    )
    return {"success": True, "received_at": datetime.now(UTC).isoformat()}


# --------------------------------------------------------------------------- #
# POST /promotions/{promotion_id}/dismiss                                     #
# --------------------------------------------------------------------------- #


class DismissRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    remember_for_days: int = Field(default=30, ge=1, le=365)


@router.post(
    "/promotions/{promotion_id}/dismiss",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Dismiss a promotion for the current visitor",
    operation_id="storefront_dismiss_promotion",
)
async def dismiss_promotion(
    request: Request,
    store_id: Annotated[UUID, Path()],
    promotion_id: Annotated[UUID, Path()],
    body: DismissRequest,
    promo_repo: Annotated[PromotionRepository, Depends(get_promotion_repository)],
    dismissal_repo: Annotated[
        PromotionDismissalRepository,
        Depends(get_promotion_dismissal_repository),
    ],
    event_repo: Annotated[
        PromotionEventRepository, Depends(get_promotion_event_repository)
    ],
    store_repo: Annotated[StoreRepository, Depends(get_store_repository)],
    customer: Annotated[Customer | None, Depends(get_optional_customer)] = None,
) -> None:
    store = await store_repo.get_by_id(store_id)
    if store is None:
        raise EntityNotFoundError("Store", str(store_id))

    visitor_token = _visitor_token(request) if customer is None else None
    if customer is None and visitor_token is None:
        # Caller is fully anonymous and didn't carry a visitor cookie —
        # we can't persist a stable suppression for them. Return 204
        # silently; the storefront's localStorage marker still applies.
        return

    use_case = DismissPromotionUseCase(
        promotion_repo=promo_repo,
        dismissal_repo=dismissal_repo,
        event_repo=event_repo,
    )
    await use_case.execute(
        tenant_id=store.tenant_id,
        store_id=store_id,
        promotion_id=promotion_id,
        customer_id=customer.id if customer else None,
        visitor_token=visitor_token,
    )
    # `remember_for_days` is honored client-side via the storefront's own
    # localStorage marker; the DB row is permanent until the merchant
    # purges old dismissals via `tasks.delete_expired_dismissals`.
    _ = body
