"""Storefront cart routes.

URL: /storefront/me/cart

Server-side cart backed by Redis for persistence across restarts
and horizontal scaling.
"""

import logging
from typing import Annotated
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, HTTPException, Path, Request, status

from src.api.dependencies.auth import get_current_customer
from src.api.dependencies.repositories import (
    get_funnel_event_repository,
    get_product_repository,
    get_store_repository,
)
from src.api.responses import SuccessResponse
from src.api.v1.schemas.storefront.cart import (
    AddCartItemRequest,
    CartItemResponse,
    CartResponse,
    UpdateCartItemRequest,
)
from src.core.entities.cart import Cart
from src.core.entities.customer import Customer
from src.core.entities.product import PURCHASABLE_STATUSES
from src.core.value_objects.cart_item import CartItem
from src.infrastructure.database.connection import get_tenant_id
from src.infrastructure.repositories import ProductRepository
from src.infrastructure.repositories.cart_repository import RedisCartRepository
from src.infrastructure.repositories.funnel_event_repository import (
    FunnelEventRepository,
)
from src.infrastructure.repositories.store_repository import StoreRepository

logger = logging.getLogger(__name__)


def client_session_fingerprint(http_request) -> str | None:
    """The storefront's client-side session fingerprint, from cookies.

    Mirrors the top of lib/meta-pixel.getSessionFingerprint's resolution
    chain (numu_attribution.session_id, then the numu_session cookie).
    Server-emitted funnel rows previously stored NULL here, which made
    every session-level funnel query undercount add_to_cart sessions —
    the events existed but belonged to nobody.
    """
    import json as _json
    from urllib.parse import unquote as _unquote

    try:
        raw = http_request.cookies.get("numu_attribution")
        if raw:
            parsed = _json.loads(_unquote(raw))
            sid = parsed.get("session_id") if isinstance(parsed, dict) else None
            if isinstance(sid, str) and sid:
                return sid[:64]
    except (ValueError, TypeError):
        pass
    sid = http_request.cookies.get("numu_session")
    return sid[:64] if sid else None


async def emit_add_to_cart_event(
    funnel_repo: FunnelEventRepository,
    store_repo: StoreRepository,
    *,
    store_id: UUID,
    customer_id: UUID | None,
    step_data: dict,
    session_fingerprint: str | None = None,
) -> None:
    """Best-effort ``add_to_cart`` funnel event — MUST NEVER break the cart.

    Two safeguards, both learned from a P0 that 500'd every guest add-to-cart:

    1. Correct ``tenant_id``. ``funnel_events.tenant_id`` FKs to ``tenants``;
       storefront guest requests carry no tenant context (``get_tenant_id()``
       is ``None``), so the old code fell back to ``store_id`` — which is NOT a
       ``tenants`` row → ForeignKeyViolation. We resolve the store's real
       owning tenant instead, and skip the event entirely if we can't.

    2. SAVEPOINT isolation. The insert runs inside ``begin_nested()`` and is
       flushed there, so a failure rolls back only the savepoint — the cart
       write + the cart-response read on the same session stay intact (a bare
       ``try/except`` left the session in a PendingRollback state → 500).
    """
    try:
        tid = get_tenant_id()
        tenant_id: UUID | None = UUID(tid) if tid else None
        if tenant_id is None:
            store = await store_repo.get_by_id(store_id)
            tenant_id = store.tenant_id if store else None
        if tenant_id is None:
            return  # can't attribute the event — skip rather than poison
        async with funnel_repo.session.begin_nested():
            await funnel_repo.create(
                tenant_id=tenant_id,
                store_id=store_id,
                step="add_to_cart",
                session_fingerprint=session_fingerprint,
                customer_id=customer_id,
                step_data=step_data,
            )
            await funnel_repo.session.flush()
    except Exception:  # noqa: BLE001 — telemetry must never break the cart
        pass


router = APIRouter()

# ---------------------------------------------------------------------------
# Redis-backed cart repository
# ---------------------------------------------------------------------------
_cart_repo = RedisCartRepository()


async def _get_or_create_cart(customer_id: UUID, store_id: UUID) -> Cart:
    """Return the customer's cart from Redis, creating a new one if needed."""
    cart = await _cart_repo.get_by_customer_id(customer_id, store_id)
    if cart is None:
        cart = Cart(
            id=uuid4(),
            session_id=str(uuid4()),
            store_id=store_id,
            customer_id=customer_id,
            currency="EGP",
        )
    return cart


async def _get_or_create_guest_cart(session_id: UUID, store_id: UUID) -> Cart:
    """Return a guest cart keyed on the storefront's `numu_cart_session`
    cookie, creating an empty one if Redis has no record. Mirrors the
    customer flow but with no customer_id binding — login can later
    merge into a customer cart via `_cart_repo.merge`.
    """
    cart = await _cart_repo.get_by_session_id(str(session_id), store_id)
    if cart is None:
        cart = Cart(
            id=uuid4(),
            session_id=str(session_id),
            store_id=store_id,
            customer_id=None,
            currency="EGP",
        )
    return cart


async def _compute_cart_discounts(
    cart: Cart,
    visible_items: list[CartItem],
    products_by_id: dict,
    product_repo: ProductRepository,
) -> tuple[int, int, list[dict]]:
    """Price the cart through the offers-v2 engine.

    Returns ``(automatic_cents, total_discount_cents, applied_promotions)``.

    ``visible_items`` is the set of lines that survived into the response —
    NOT ``cart.items``. A line whose product was deleted or archived while
    sitting in the cart is dropped from `items`/`subtotal`, and it must be
    dropped from the pricing too. Pricing the raw cart instead would let an
    invisible unit complete a group: the cart would promise a trio discount
    the shopper can't see the third item for, then checkout — which only
    submits visible lines — would find two units, apply nothing, and charge
    more than the cart displayed.

    This is the SAME `CalculateCartDiscountsUseCase` that
    `POST /storefront/store/{id}/cart/discounts` previews with and that
    checkout charges with, so the three can't disagree. Two details make
    that guarantee real:

    * Lines carry `category_id` (read off the products we already loaded —
      no extra query), otherwise category-scoped rules silently match
      nothing here while matching at checkout.
    * Any code pinned on the cart is passed through, so a promotion-backed
      code prices the same in the cart as it does at checkout. Plain legacy
      coupons are NOT recomputed here — they have no promotion behind them,
      so the engine returns 0 for them; the checkout summary keeps using
      `/cart/discounts`, which carries the legacy-parity shim.

    Best-effort by design: any failure returns zeroes and the cart renders
    at full price. A promotions outage must never make the cart
    unreachable — same contract as the checkout path, which wraps this use
    case in the identical try/except.
    """
    if not visible_items:
        return 0, 0, []

    # Local imports mirror the checkout path: keeps the promotions engine
    # out of the cart module's import graph for every request that never
    # reaches this branch.
    from src.application.dto.promotion_resolution import VisitorContextInput
    from src.application.use_cases.promotions.calculate_cart_discounts import (
        CalculateCartDiscountsUseCase,
    )
    from src.core.services.discount_calculator import DiscountCalculator
    from src.core.services.promotion_eligibility_checker import (
        PromotionEligibilityChecker,
    )
    from src.infrastructure.repositories.coupon_repository import CouponRepository
    from src.infrastructure.repositories.promotion_event_repository import (
        PromotionEventRepository,
    )
    from src.infrastructure.repositories.promotion_repository import (
        PromotionRepository,
        PromotionTargetRepository,
    )

    # Products are tenant-scoped, so the owning tenant comes free off one we
    # already loaded — no store lookup needed.
    _first = products_by_id.get(visible_items[0].product_id)
    tenant_id = getattr(_first, "tenant_id", None) if _first else None

    # Rebuild the visible lines with category ids attached. The products are
    # already in memory from the response build above.
    priced_cart = cart.model_copy(
        update={
            "items": [
                ci.model_copy(
                    update={
                        "category_id": getattr(
                            products_by_id.get(ci.product_id), "category_id", None
                        )
                    }
                )
                for ci in visible_items
            ]
        }
    )

    subtotal_cents = sum(ci.unit_price * ci.quantity for ci in priced_cart.items)
    category_ids = [
        cid for cid in {ci.category_id for ci in priced_cart.items} if cid is not None
    ]
    visitor = VisitorContextInput(
        customer_id=cart.customer_id,
        is_logged_in=cart.customer_id is not None,
        cart_subtotal_cents=subtotal_cents,
        cart_product_ids=[ci.product_id for ci in priced_cart.items],
        cart_category_ids=category_ids,
    )

    session = product_repo.session
    use_case = CalculateCartDiscountsUseCase(
        promotion_repo=PromotionRepository(session),
        target_repo=PromotionTargetRepository(session),
        coupon_repo=CouponRepository(session),
        eligibility_checker=PromotionEligibilityChecker(),
        calculator=DiscountCalculator(),
        event_repo=PromotionEventRepository(session),
    )
    # NOTE (verified 2026-07-28): `Cart` declares no `discount_code` field, and
    # both `POST/DELETE /cart/discount` guard their writes with
    # `hasattr(cart, "discount_code")` — which is always False. So no code is
    # ever actually pinned to a cart today and this resolves to None. Reading
    # it defensively means the cart starts pricing codes the moment that field
    # is added, instead of silently continuing to ignore them.
    pinned_code = getattr(cart, "discount_code", None)
    out = await use_case.execute(
        store_id=cart.store_id,
        tenant_id=tenant_id,
        cart=priced_cart,
        applied_coupon_codes=[pinned_code] if pinned_code else [],
        visitor=visitor,
    )
    total_discount = min(
        out.automatic_discount_cents + out.code_discount_cents, subtotal_cents
    )
    return out.automatic_discount_cents, total_discount, list(out.applied_promotions)


async def _build_cart_response(
    cart: Cart,
    product_repo: ProductRepository,
) -> SuccessResponse[CartResponse]:
    """Build a CartResponse from a Cart entity.

    Uses the snapshotted `cart_item.unit_price` (captured when the line
    was added) for the subtotal — NOT the live product price. This
    matches Shopify behavior: a merchant editing prices mid-session
    must not change a customer's existing cart total in real time.

    Live product data (current price, current inventory) is exposed as
    `current_price` / `available_now` / `sold_out_now` / `price_changed`
    so themes can surface "price changed" or "reduce quantity" nudges.

    Step 08 N+1 fix: a single ``product_repo.get_by_ids(...)`` call
    replaces the per-item ``get_by_id`` loop that previously emitted
    one (product + selectin store + selectin category) trio per cart
    line.
    """
    items: list[CartItemResponse] = []
    # The same lines, as domain value objects — the offers engine must price
    # exactly what the shopper can see, not the raw cart (see
    # `_compute_cart_discounts`).
    visible_items: list[CartItem] = []
    subtotal = 0

    unique_product_ids = list({item.product_id for item in cart.items})
    products = await product_repo.get_by_ids(unique_product_ids)
    products_by_id = {p.id: p for p in products}

    for cart_item in cart.items:
        product = products_by_id.get(cart_item.product_id)
        if not product or product.status not in PURCHASABLE_STATUSES:
            continue
        visible_items.append(cart_item)

        # Snapshot — what the customer agreed to pay when they added the
        # line. CartItem.unit_price is set at add-time in add_cart_item.
        unit_price = cart_item.unit_price
        total_price = unit_price * cart_item.quantity
        subtotal += total_price

        # Live deltas — the front-end uses these to render "price changed"
        # banners and to disable Checkout when any line is sold-out.
        # effective_price, not `.price`: a scheduled sale that has opened
        # since the line was added must show as a price CHANGE, otherwise
        # the banner never fires and the customer keeps the old price.
        current_price = product.effective_price().cents
        price_changed = current_price != unit_price
        available_now = product.quantity if product.quantity is not None else None
        sold_out_now = not product.is_in_stock

        items.append(
            CartItemResponse(
                id=cart_item.item_key,
                product_id=str(product.id),
                product_name=product.name,
                variant_id=str(cart_item.variant_id) if cart_item.variant_id else None,
                variant_name=cart_item.variant_name,
                sku=product.sku,
                quantity=cart_item.quantity,
                unit_price=unit_price,
                total_price=total_price,
                current_price=current_price,
                price_changed=price_changed,
                image_url=product.images[0] if product.images else None,
                category_id=(str(product.category_id) if product.category_id else None),
                in_stock=product.is_in_stock,
                available_now=available_now,
                sold_out_now=sold_out_now,
            )
        )

    currency = "EGP"
    if items and cart.items:
        first_product = products_by_id.get(cart.items[0].product_id)
        if first_product:
            currency = first_product.price.currency.value

    # Offers-v2: price the cart with the same engine checkout charges with, so
    # the shopper sees the discount fire instead of discovering it one step
    # later. Best-effort — a promotions failure must never break the cart.
    automatic_cents = 0
    discount_amount = 0
    applied_promotions: list[dict] = []
    try:
        (
            automatic_cents,
            discount_amount,
            applied_promotions,
        ) = await _compute_cart_discounts(
            cart, visible_items, products_by_id, product_repo
        )
        # The engine already caps at its own subtotal — which is this same
        # visible set — so this only binds if the two ever drift apart again.
        # Keeps `subtotal - discount_amount == total` true for any theme that
        # renders all three.
        discount_amount = min(discount_amount, subtotal)
        automatic_cents = min(automatic_cents, discount_amount)
    except Exception as exc:  # noqa: BLE001 — offers must never block the cart
        logger.warning("cart_discounts_error store=%s err=%s", cart.store_id, exc)

    return SuccessResponse(
        data=CartResponse(
            items=items,
            item_count=len(items),
            total_quantity=sum(i.quantity for i in items),
            subtotal=subtotal,
            currency=currency,
            automatic_discount_cents=automatic_cents,
            discount_amount=discount_amount,
            total=max(0, subtotal - discount_amount),
            applied_promotions=applied_promotions,
        ),
        message="Cart retrieved successfully",
    )


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@router.get(
    "/cart",
    response_model=SuccessResponse[CartResponse],
    summary="Get cart",
    operation_id="get_cart",
)
async def get_cart(
    current_customer: Annotated[Customer, Depends(get_current_customer)],
    product_repo: Annotated[ProductRepository, Depends(get_product_repository)],
):
    """Return the current customer's cart with live product data."""
    cart = await _get_or_create_cart(current_customer.id, current_customer.store_id)
    return await _build_cart_response(cart, product_repo)


@router.post(
    "/cart/items",
    response_model=SuccessResponse[CartResponse],
    status_code=status.HTTP_201_CREATED,
    summary="Add item to cart",
    operation_id="add_cart_item",
)
async def add_cart_item(
    request: AddCartItemRequest,
    http_request: Request,
    current_customer: Annotated[Customer, Depends(get_current_customer)],
    product_repo: Annotated[ProductRepository, Depends(get_product_repository)],
    funnel_repo: Annotated[FunnelEventRepository, Depends(get_funnel_event_repository)],
    store_repo: Annotated[StoreRepository, Depends(get_store_repository)],
):
    """Add a product to the customer's cart.

    If the same product (+ variant) already exists in the cart,
    the quantity is incremented instead of creating a duplicate entry.
    """
    # Validate product exists, is active, and belongs to the customer's store
    product = await product_repo.get_by_id(request.product_id)
    if not product:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Product not found",
        )
    if product.status not in PURCHASABLE_STATUSES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Product is not available",
        )
    if product.store_id != current_customer.store_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Product does not belong to this store",
        )
    if not product.is_in_stock:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Product is out of stock",
        )

    cart = await _get_or_create_cart(current_customer.id, current_customer.store_id)

    # Phase 8.1 — resolve the variant. Add-to-cart now requires a
    # variant_id; for single-variant products the client sends the
    # default variant's id (or the storefront /products/{slug} response
    # surfaces it via `variants[0].id`). Falling back to product.price
    # is still supported transitionally for clients that haven't
    # picked up the new shape.
    variant_price_cents = product.effective_price().cents
    variant_sku = product.sku
    variant_image: str | None = product.images[0] if product.images else None
    variant_name: str | None = None
    if request.variant_id:
        from src.infrastructure.database.connection import AsyncSessionLocal
        from src.infrastructure.repositories.variant_repository import (
            VariantRepository,
        )

        async with AsyncSessionLocal() as _s:
            variant = await VariantRepository(_s).get_by_id(request.variant_id)
        if variant is None or variant.product_id != product.id:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Variant not found for this product.",
            )
        # `product.variant_is_in_stock`, not `variant.is_in_stock`: the
        # oversell flag lives on the product, and the variant cannot see it.
        if not product.variant_is_in_stock(variant):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="This variant is out of stock.",
            )
        # Variant Money now matches product Money: `.amount` is MAJOR units,
        # `.cents` the smallest unit. Use `.cents` for the cents unit price.
        variant_price_cents = variant.price.cents
        variant_sku = variant.sku or product.sku
        if variant.image_url:
            variant_image = variant.image_url
        if variant.option_values:
            variant_name = " / ".join(
                str(v) for v in variant.option_values.values() if v
            )

    # Cap the added quantity to what's still available so repeated adds can't
    # push a line past the variant's inventory (add_item increments any
    # existing line). Product-level (no-variant) lines keep product stock rules.
    add_qty = request.quantity
    # An overselling product has no ceiling to cap against — that is the whole
    # point of the flag — so the cap only applies when inventory is being
    # tracked. Without this the cap re-imposes the sold-out behaviour the
    # guard above just lifted.
    if (
        request.variant_id
        and variant is not None
        and not product.continue_selling_when_out_of_stock
    ):
        existing = cart.get_item(request.product_id, request.variant_id)
        existing_qty = existing.quantity if existing else 0
        allowed = max(0, variant.inventory_quantity - existing_qty)
        if allowed <= 0:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=(
                    f"Only {variant.inventory_quantity} in stock — you already "
                    "have the maximum in your cart."
                ),
            )
        add_qty = min(request.quantity, allowed)

    # Fallback label: the picker's selected axes for products whose variant
    # rows carry no option_values (or none resolved) — mirrors the SDK alias
    # route so the choice survives into cart/checkout/order/email/invoice.
    if not variant_name and request.selected_options:
        variant_name = (
            " / ".join(str(v) for v in request.selected_options.values() if v) or None
        )

    new_item = CartItem(
        product_id=request.product_id,
        product_name=product.name,
        variant_id=request.variant_id,
        variant_name=variant_name,
        quantity=add_qty,
        unit_price=variant_price_cents,
        sku=variant_sku,
        image_url=variant_image,
    )
    cart.add_item(new_item)
    await _cart_repo.save(cart)

    # Emit funnel event (best-effort, savepoint-isolated, correct tenant_id)
    await emit_add_to_cart_event(
        funnel_repo,
        store_repo,
        store_id=current_customer.store_id,
        customer_id=current_customer.id,
        session_fingerprint=client_session_fingerprint(http_request),
        step_data={
            "product_id": str(request.product_id),
            "product_name": product.name,
            "quantity": request.quantity,
            "unit_price": product.effective_price().cents,
        },
    )

    return await _build_cart_response(cart, product_repo)


@router.patch(
    "/cart/items/{item_id}",
    response_model=SuccessResponse[CartResponse],
    summary="Update cart item quantity",
    operation_id="update_cart_item",
)
async def update_cart_item(
    item_id: Annotated[
        str, Path(description="Cart item ID (product_id or product_id:variant_id)")
    ],
    request: UpdateCartItemRequest,
    current_customer: Annotated[Customer, Depends(get_current_customer)],
    product_repo: Annotated[ProductRepository, Depends(get_product_repository)],
):
    """Update the quantity of a cart item."""
    cart = await _get_or_create_cart(current_customer.id, current_customer.store_id)

    # Parse item_id to extract product_id and optional variant_id
    parts = item_id.split(":")
    product_id = UUID(parts[0])
    variant_id = UUID(parts[1]) if len(parts) > 1 else None

    existing = cart.get_item(product_id, variant_id)
    if not existing:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Cart item not found",
        )

    cart.update_item_quantity(product_id, request.quantity, variant_id)
    await _cart_repo.save(cart)

    return await _build_cart_response(cart, product_repo)


@router.delete(
    "/cart/items/{item_id}",
    response_model=SuccessResponse[CartResponse],
    summary="Remove item from cart",
    operation_id="remove_cart_item",
)
async def remove_cart_item(
    item_id: Annotated[
        str, Path(description="Cart item ID (product_id or product_id:variant_id)")
    ],
    current_customer: Annotated[Customer, Depends(get_current_customer)],
    product_repo: Annotated[ProductRepository, Depends(get_product_repository)],
):
    """Remove a single item from the cart."""
    cart = await _get_or_create_cart(current_customer.id, current_customer.store_id)

    # Parse item_id to extract product_id and optional variant_id
    parts = item_id.split(":")
    product_id = UUID(parts[0])
    variant_id = UUID(parts[1]) if len(parts) > 1 else None

    existing = cart.get_item(product_id, variant_id)
    if not existing:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Cart item not found",
        )

    cart.remove_item(product_id, variant_id)
    await _cart_repo.save(cart)

    return await _build_cart_response(cart, product_repo)


@router.delete(
    "/cart",
    response_model=SuccessResponse[CartResponse],
    summary="Clear cart",
    operation_id="clear_cart",
)
async def clear_cart(
    current_customer: Annotated[Customer, Depends(get_current_customer)],
    product_repo: Annotated[ProductRepository, Depends(get_product_repository)],
):
    """Remove all items from the customer's cart."""
    await _cart_repo.delete_by_customer_id(
        current_customer.id, current_customer.store_id
    )
    return SuccessResponse(
        data=CartResponse(
            items=[],
            item_count=0,
            total_quantity=0,
            subtotal=0,
            currency="EGP",
        ),
        message="Cart cleared successfully",
    )
