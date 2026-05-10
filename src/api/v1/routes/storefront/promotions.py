"""Public storefront routes for the offers-v2 / Promotions feature.

URL: /storefront/store/{store_id}/promotions/...

These routes are publicly accessible (no merchant auth):
* `GET .../active` — the hot read path the bazaar fetches once per
  page-load to know what to render.
* `POST .../{promotion_id}/events` — fire-and-forget analytics writes.
* `POST .../{promotion_id}/dismiss` — record a per-visitor / per-customer
  suppression so the same shopper isn't nagged twice.
* `POST .../{promotion_id}/submit` — capture the popup / floating-widget
  email (and optional phone) form, recording a `submit` event with the
  PII as metadata and returning the configured discount code.

The cart-side `apply coupon v2` flow lives in [coupon.py](./coupon.py) and
is tightly coupled to the cart code path; that integration is deferred
to step 12 of the offers-v2 plan (storefront checkout discounts).
"""

from datetime import UTC, datetime
from typing import Annotated, Any, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Path, Request, status
from pydantic import BaseModel, ConfigDict, EmailStr, Field

from src.api.dependencies.auth import get_optional_customer
from src.api.dependencies.feature_flags import require_feature_flag
from src.api.dependencies.promotion_preview import maybe_preview_for_store
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
    CartDiscountsOutput,
    VisitorContextInput,
)
from src.application.use_cases.promotions.calculate_cart_discounts import (
    CalculateCartDiscountsUseCase,
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
from src.core.entities.cart import Cart
from src.core.entities.customer import Customer
from src.core.enums.promotion_enums import PromotionEventType
from src.core.exceptions import EntityNotFoundError
from src.core.services.discount_calculator import DiscountCalculator
from src.core.services.promotion_eligibility_checker import (
    PromotionEligibilityChecker,
)
from src.core.services.promotion_resolver import PromotionResolver
from src.core.value_objects.cart_item import CartItem
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
    is_preview: Annotated[bool, Depends(maybe_preview_for_store)],
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
        store_id=store_id,
        tenant_id=store.tenant_id,
        visitor=visitor,
        preview=is_preview,
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


# --------------------------------------------------------------------------- #
# POST /promotions/{promotion_id}/submit                                      #
# --------------------------------------------------------------------------- #


class SubmitFormRequest(BaseModel):
    """Visitor-submitted form payload for a popup / floating widget.

    Phone is optional — most merchants only collect email; the WhatsApp-
    heavy Egyptian market sometimes wants both. `accepts_marketing` is
    the honest "I agree to receive updates" checkbox. Form-fields beyond
    these three live in `extra_fields` so a merchant can collect "name"
    or "size preference" without a schema change.
    """

    model_config = ConfigDict(extra="forbid")

    email: EmailStr = Field(description="Visitor email address.")
    phone: str | None = Field(
        default=None,
        max_length=20,
        description="Egyptian phone (01xxxxxxxxx) — optional.",
    )
    accepts_marketing: bool = Field(
        default=False,
        description="Whether the visitor opted in to marketing messages.",
    )
    extra_fields: dict[str, str] = Field(
        default_factory=dict,
        description="Additional ad-hoc fields configured by the merchant.",
    )


class SubmitFormResponse(BaseModel):
    """Server response after capturing a popup form submission.

    `discount_code` is populated when the promotion's `content` is
    configured with a `discount_code_to_reveal` — the storefront flips
    its popup into the success state and shows the code with a copy
    button.
    """

    discount_code: str | None = Field(
        default=None,
        description="Code to reveal after a successful submission.",
    )
    received_at: str = Field(description="ISO-8601 server-side receipt time.")


@router.post(
    "/promotions/{promotion_id}/submit",
    response_model=SuccessResponse[SubmitFormResponse],
    status_code=status.HTTP_201_CREATED,
    summary="Capture a popup / floating-widget form submission",
    operation_id="storefront_submit_promotion_form",
)
async def submit_form(
    request: Request,
    store_id: Annotated[UUID, Path()],
    promotion_id: Annotated[UUID, Path()],
    body: SubmitFormRequest,
    promo_repo: Annotated[PromotionRepository, Depends(get_promotion_repository)],
    event_repo: Annotated[
        PromotionEventRepository, Depends(get_promotion_event_repository)
    ],
    store_repo: Annotated[StoreRepository, Depends(get_store_repository)],
    customer: Annotated[Customer | None, Depends(get_optional_customer)] = None,
) -> SuccessResponse[SubmitFormResponse]:
    """Record a popup form submission and return the reveal code.

    The submission is persisted as a `promotion_events` row with
    `event_type = 'submit'`; the email / phone / extra fields go into
    the row's metadata blob so analytics queries and right-to-be-
    forgotten deletion both flow through one path.

    The endpoint refuses to record a submit for a promotion that
    doesn't belong to the request's store — defensive, since the public
    URL otherwise lets any visitor blast email captures into any
    promotion id they discover.
    """
    store = await store_repo.get_by_id(store_id)
    if store is None:
        raise EntityNotFoundError("Store", str(store_id))

    promotion = await promo_repo.get_by_id(store_id=store_id, promotion_id=promotion_id)
    if promotion is None or promotion.store_id != store_id:
        # Use 404 rather than 403 so we don't confirm to a curious
        # caller that a given promotion id exists in a different store.
        raise EntityNotFoundError("Promotion", str(promotion_id))

    use_case = RecordPromotionEventUseCase(
        promotion_repo=promo_repo, event_repo=event_repo
    )
    metadata: dict[str, Any] = {
        "email": str(body.email),
        "accepts_marketing": body.accepts_marketing,
    }
    if body.phone:
        metadata["phone"] = body.phone
    if body.extra_fields:
        metadata["extra_fields"] = body.extra_fields

    await use_case.execute(
        tenant_id=store.tenant_id,
        store_id=store_id,
        promotion_id=promotion_id,
        event_type=PromotionEventType.SUBMIT,
        customer_id=customer.id if customer else None,
        session_id=_visitor_token(request),
        metadata=metadata,
    )

    # Reveal the merchant-configured discount code (if any). The
    # promotion's `content` payload is a free-form dict — merchant hub
    # writes `discount_code_to_reveal` for popups that gate a code
    # behind the form. If the merchant misconfigured the popup with
    # form fields but no reveal code, we still persist the lead and
    # return a null code so the storefront falls back to a generic
    # thanks-screen.
    content = promotion.content.model_dump() if promotion.content else {}
    reveal_code: str | None = content.get("discount_code_to_reveal")

    return SuccessResponse(
        data=SubmitFormResponse(
            discount_code=reveal_code,
            received_at=datetime.now(UTC).isoformat(),
        ),
        message="Submission received",
    )


# --------------------------------------------------------------------------- #
# POST /cart/discounts                                                        #
# --------------------------------------------------------------------------- #


class CartDiscountsLineItem(BaseModel):
    """A single cart line for discount evaluation.

    Carries just enough to drive `DiscountRule.calculate()` —
    `unit_price_cents` is integer-cents because every discount math
    (BOGO cheapest-unit, tiered threshold, percentage rounding) is
    integer-only on the server side. The storefront sends the price
    it already shows the customer, so the math the customer sees in
    the cart matches what the server records on the order.
    """

    model_config = ConfigDict(extra="forbid")

    product_id: UUID
    quantity: int = Field(ge=1, le=999)
    unit_price_cents: int = Field(ge=0)
    category_id: UUID | None = None


class CartDiscountsRequest(BaseModel):
    """Body of `POST /storefront/store/{id}/cart/discounts`.

    The storefront calls this every time the cart changes — it's the
    single source of truth for the discount line shown in the cart
    drawer, the checkout summary, and (re-evaluated) at order-create.
    Stateless: line items + applied codes in, totals + applied promos
    out. The server is the authoritative computation; the client never
    runs its own copy of the rule engine.
    """

    model_config = ConfigDict(extra="forbid")

    items: list[CartDiscountsLineItem] = Field(default_factory=list, max_length=200)
    applied_codes: list[str] = Field(default_factory=list, max_length=10)
    visitor: VisitorContextInput | None = None


@router.post(
    "/cart/discounts",
    response_model=SuccessResponse[CartDiscountsOutput],
    summary="Recompute the cart's discount totals",
    operation_id="storefront_calculate_cart_discounts",
)
async def calculate_cart_discounts(
    request: Request,
    store_id: Annotated[UUID, Path()],
    body: CartDiscountsRequest,
    promo_repo: Annotated[PromotionRepository, Depends(get_promotion_repository)],
    target_repo: Annotated[
        PromotionTargetRepository, Depends(get_promotion_target_repository)
    ],
    coupon_repo: Annotated[Any, Depends(get_coupon_repository)],
    store_repo: Annotated[StoreRepository, Depends(get_store_repository)],
    customer: Annotated[Customer | None, Depends(get_optional_customer)] = None,
) -> SuccessResponse[CartDiscountsOutput]:
    """Stateless cart-discount calculator.

    The storefront fires this whenever the cart contents change so the
    UI shows the same number the server will charge. BOGO + tiered
    rules need full line-item context to compute (cheapest-unit,
    bundle-counting, threshold matching), so the price-only
    `/coupons/apply` path can't replace this — that route stays for
    legacy percentage / fixed coupons but the cart-side total now
    flows through here.
    """
    store = await store_repo.get_by_id(store_id)
    if store is None:
        raise EntityNotFoundError("Store", str(store_id))

    visitor = body.visitor or VisitorContextInput(
        customer_id=customer.id if customer else None,
        visitor_token=_visitor_token(request),
        is_logged_in=customer is not None,
        cart_subtotal_cents=sum(li.unit_price_cents * li.quantity for li in body.items),
        cart_product_ids=[li.product_id for li in body.items],
        cart_category_ids=[
            li.category_id for li in body.items if li.category_id is not None
        ],
    )

    # Hydrate a Cart entity from the request payload. We don't persist
    # anything — the entity is just the shape the use case expects so
    # we can reuse the same path the order-create flow uses.
    cart = Cart(
        session_id=_visitor_token(request) or "anon",
        store_id=store_id,
        customer_id=customer.id if customer else None,
        items=[
            CartItem(
                product_id=li.product_id,
                product_name="",  # not needed for math
                quantity=li.quantity,
                unit_price=li.unit_price_cents,
            )
            for li in body.items
        ],
    )

    use_case = CalculateCartDiscountsUseCase(
        promotion_repo=promo_repo,
        target_repo=target_repo,
        coupon_repo=coupon_repo,
        eligibility_checker=PromotionEligibilityChecker(),
        calculator=DiscountCalculator(),
    )
    out = await use_case.execute(
        store_id=store_id,
        tenant_id=store.tenant_id,
        cart=cart,
        applied_coupon_codes=body.applied_codes,
        visitor=visitor,
    )
    return SuccessResponse(data=out)
