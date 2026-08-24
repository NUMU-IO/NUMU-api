"""SDK-style cart aliases at /storefront/cart/*.

The @numu/theme-sdk's NuMuProvider issues these calls:
  GET    /api/cart            (Next.js storefront proxy → here)
  POST   /api/cart/add        (proxy → /storefront/cart/add)
  POST   /api/cart/remove     (proxy → /storefront/cart/remove)
  POST   /api/cart/update     (proxy → /storefront/cart/update)
  POST   /api/cart/discount   (proxy → /storefront/cart/discount)
  DELETE /api/cart/discount   (proxy → /storefront/cart/discount)

Guest cart support: routes use the `get_cart_owner` dependency, which
resolves to either an authenticated `Customer` (cookie-based session)
OR a guest `numu_cart_session` cookie. Anonymous shoppers can browse,
add to cart, and persist their cart across page reloads without
creating an account. Login flow can merge a guest cart into the
customer cart via `RedisCartRepository.merge`.

All cart writes are guarded by the storefront's double-submit CSRF
proxy (cart-proxy.ts in numu-storefront). Idempotency keys are
forwarded but only honored by routes that opt in.
"""

from __future__ import annotations

import logging
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.dependencies.repositories import (
    get_coupon_repository,
    get_funnel_event_repository,
    get_product_repository,
    get_store_repository,
)
from src.api.responses import SuccessResponse
from src.api.v1.routes.storefront._cart_owner import CartOwner, get_cart_owner
from src.api.v1.routes.storefront.cart import (
    _build_cart_response,
    _cart_repo,
    _get_or_create_cart,
    _get_or_create_guest_cart,
    client_session_fingerprint,
    emit_add_to_cart_event,
)
from src.api.v1.schemas.storefront.cart import CartResponse
from src.core.entities.product import PURCHASABLE_STATUSES
from src.core.value_objects.cart_item import CartItem
from src.infrastructure.database.connection import get_admin_db_session
from src.infrastructure.repositories import ProductRepository
from src.infrastructure.repositories.coupon_repository import CouponRepository
from src.infrastructure.repositories.funnel_event_repository import (
    FunnelEventRepository,
)
from src.infrastructure.repositories.store_repository import StoreRepository

logger = logging.getLogger(__name__)

router = APIRouter()


# ─── Helper: resolve cart for owner ──────────────────────────────────────────


async def _get_cart_for(owner: CartOwner):
    """Return the cart row for either an authenticated customer or a
    guest session, creating an empty one if none exists. Single point
    for the customer/guest fork keeps the route bodies readable.
    """
    if owner.is_guest:
        # Guest: keyed on `numu_cart_session` UUID.
        return await _get_or_create_guest_cart(owner.session_id, owner.store_id)
    # Authenticated buyer: keyed on customer_id.
    assert owner.customer_id is not None  # narrowed by is_guest=False
    return await _get_or_create_cart(owner.customer_id, owner.store_id)


# ─── SDK request bodies ──────────────────────────────────────────────────────


class SdkAddItemRequest(BaseModel):
    product_id: UUID
    variant_id: UUID | None = None
    quantity: int = Field(default=1, ge=1)
    # Selected option axes ({"Color": "Black", "Size": "L"}) sent by the SDK
    # when the theme's picker can't resolve a real variant row — legacy
    # products keep their axes in attributes JSON with a single placeholder
    # variant whose option_values is {}. Used only as a variant_name fallback;
    # pricing/stock still come from the variant row (or product) as before.
    selected_options: dict[str, str] | None = None


class SdkRemoveItemRequest(BaseModel):
    item_id: str = Field(
        description="Cart item key (product_id or product_id:variant_id)"
    )


class SdkUpdateItemRequest(BaseModel):
    item_id: str | None = None
    quantity: int | None = Field(default=None, ge=0)
    note: str | None = None


class SdkDiscountRequest(BaseModel):
    code: str = Field(min_length=1, max_length=64)


class SdkRecoverCartRequest(BaseModel):
    # An `abandoned_checkouts.id` (merchant manual nudge) OR a `customer_id`
    # whose Redis cart is still live (scheduled auto-detect job). Resolved in
    # that order — see `_resolve_recover_line_items`.
    recover_id: UUID


async def _resolve_recover_line_items(
    recover_id: UUID,
    store_id: UUID,
    admin_db: AsyncSession,
) -> list[dict]:
    """Line items for a recovery id, scoped to this store.

    Tries the persisted `abandoned_checkouts` row first (the merchant's
    manual WhatsApp nudge keys on the checkout id), then falls back to a
    live Redis customer cart (the scheduled auto-detect job keys on
    customer_id). Returns ``[]`` when neither resolves for this store — a
    wrong-store / expired / unknown id restores nothing rather than
    erroring. Read via the admin (RLS-bypassing) session because the
    shopper clicking the link carries no tenant context, and we enforce
    `store_id` ourselves.
    """
    from src.infrastructure.database.models.tenant.abandoned_checkout import (
        AbandonedCheckoutModel,
    )

    row = (
        await admin_db.execute(
            select(AbandonedCheckoutModel).where(
                AbandonedCheckoutModel.id == recover_id,
                AbandonedCheckoutModel.store_id == store_id,
            )
        )
    ).scalar_one_or_none()
    if row is not None and row.recovered_at is not None:
        # Already converted into an order. Restoring it would put items the
        # shopper has just paid for back in their cart, and a second checkout
        # from the same link is a duplicate order waiting to happen. An empty
        # restore is the safe answer — they keep whatever is in their cart now.
        logger.info(
            "recover_link_already_recovered",
            extra={
                "checkout_id": str(recover_id),
                "order_id": str(row.recovered_order_id or ""),
            },
        )
        return []
    if row and row.line_items:
        return list(row.line_items)

    # Fallback: a live Redis customer cart (auto-detect path).
    cart = await _cart_repo.get_by_customer_id(recover_id, store_id)
    if cart and cart.items:
        return [
            {
                "product_id": str(ci.product_id),
                "variant_id": str(ci.variant_id) if ci.variant_id else None,
                "quantity": ci.quantity,
            }
            for ci in cart.items
        ]
    return []


# ─── Routes ──────────────────────────────────────────────────────────────────


@router.get(
    "/cart",
    response_model=SuccessResponse[CartResponse],
    summary="Get cart (SDK alias)",
    operation_id="sdk_get_cart",
)
async def sdk_get_cart(
    owner: Annotated[CartOwner, Depends(get_cart_owner)],
    product_repo: Annotated[ProductRepository, Depends(get_product_repository)],
):
    """Return the current cart with live product data — works for
    guests (via `numu_cart_session` cookie) and logged-in customers."""
    cart = await _get_cart_for(owner)
    return await _build_cart_response(cart, product_repo)


@router.post(
    "/cart/add",
    response_model=SuccessResponse[CartResponse],
    status_code=status.HTTP_201_CREATED,
    summary="Add item to cart (SDK alias)",
    operation_id="sdk_add_cart_item",
)
async def sdk_add_cart_item(
    request: SdkAddItemRequest,
    http_request: Request,
    owner: Annotated[CartOwner, Depends(get_cart_owner)],
    product_repo: Annotated[ProductRepository, Depends(get_product_repository)],
    funnel_repo: Annotated[FunnelEventRepository, Depends(get_funnel_event_repository)],
    store_repo: Annotated[StoreRepository, Depends(get_store_repository)],
):
    product = await product_repo.get_by_id(request.product_id)
    if not product:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Product not found"
        )
    if product.status not in PURCHASABLE_STATUSES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Product is not available",
        )
    if product.store_id != owner.store_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Product does not belong to this store",
        )
    if not product.is_in_stock:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Product is out of stock",
        )

    cart = await _get_cart_for(owner)

    # Phase 8.1 — when a variant is chosen, snapshot the VARIANT's price /
    # sku / image and carry a human `variant_name` (e.g. "L" or "L / Red").
    # Without this the line bills the product price, shows the product sku,
    # and renders with no size in the cart drawer + checkout summary.
    unit_price_cents = product.effective_price().cents
    line_sku = product.sku
    line_image = product.images[0] if product.images else None
    variant_name: str | None = None
    add_qty = request.quantity
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
        # `.cents` the smallest unit. Use `.cents` for the cart's cents price.
        unit_price_cents = variant.price.cents
        line_sku = variant.sku or product.sku
        if variant.image_url:
            line_image = variant.image_url
        if variant.option_values:
            variant_name = " / ".join(
                str(v) for v in variant.option_values.values() if v
            )
        # Enforce stock against the cart TOTAL, not just this add — repeated
        # adds and the in-cart stepper would otherwise push a line past the
        # variant's inventory. `add_item` increments any existing line, so cap
        # the delta to what's still available.
        # An overselling product has no ceiling to cap against — skip the cap
        # entirely rather than let it re-impose the sold-out behaviour the
        # guard above just lifted.
        existing = cart.get_item(request.product_id, request.variant_id)
        existing_qty = existing.quantity if existing else 0
        allowed = (
            request.quantity
            if product.continue_selling_when_out_of_stock
            else max(0, variant.inventory_quantity - existing_qty)
        )
        if allowed <= 0:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=(
                    f"Only {variant.inventory_quantity} in stock — you already "
                    "have the maximum in your cart."
                ),
            )
        add_qty = min(request.quantity, allowed)

    # Fallback label: the picker's selected axes ("Color: Black, Size: L")
    # for products whose variant rows carry no option_values (or no variant
    # resolved at all). Keeps the choice visible in cart → checkout → order
    # → email/invoice even for legacy attributes-JSON products.
    if not variant_name and request.selected_options:
        variant_name = (
            " / ".join(str(v) for v in request.selected_options.values() if v) or None
        )

    cart.add_item(
        CartItem(
            product_id=request.product_id,
            product_name=product.name,
            variant_id=request.variant_id,
            variant_name=variant_name,
            quantity=add_qty,
            unit_price=unit_price_cents,
            sku=line_sku,
            image_url=line_image,
        )
    )
    await _cart_repo.save(cart)

    # Best-effort funnel event — savepoint-isolated + correct tenant_id so it
    # can never poison the cart write (see emit_add_to_cart_event). customer_id
    # is omitted for guest carts; we still log the store + session_id.
    await emit_add_to_cart_event(
        funnel_repo,
        store_repo,
        store_id=owner.store_id,
        customer_id=owner.customer_id,
        session_fingerprint=client_session_fingerprint(http_request),
        step_data={
            "product_id": str(request.product_id),
            "product_name": product.name,
            "quantity": request.quantity,
            "unit_price": product.effective_price().cents,
            "is_guest": owner.is_guest,
            "session_id": str(owner.session_id) if owner.session_id else None,
        },
    )

    return await _build_cart_response(cart, product_repo)


@router.post(
    "/cart/remove",
    response_model=SuccessResponse[CartResponse],
    summary="Remove item from cart (SDK alias)",
    operation_id="sdk_remove_cart_item",
)
async def sdk_remove_cart_item(
    request: SdkRemoveItemRequest,
    owner: Annotated[CartOwner, Depends(get_cart_owner)],
    product_repo: Annotated[ProductRepository, Depends(get_product_repository)],
):
    cart = await _get_cart_for(owner)

    parts = request.item_id.split(":")
    try:
        product_id = UUID(parts[0])
        variant_id = UUID(parts[1]) if len(parts) > 1 else None
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid item_id"
        )

    if not cart.get_item(product_id, variant_id):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Cart item not found"
        )

    cart.remove_item(product_id, variant_id)
    await _cart_repo.save(cart)
    return await _build_cart_response(cart, product_repo)


@router.post(
    "/cart/update",
    response_model=SuccessResponse[CartResponse],
    summary="Update cart item / cart-level note (SDK alias)",
    operation_id="sdk_update_cart_item",
)
async def sdk_update_cart_item(
    request: SdkUpdateItemRequest,
    owner: Annotated[CartOwner, Depends(get_cart_owner)],
    product_repo: Annotated[ProductRepository, Depends(get_product_repository)],
):
    """Two modes:
      - `{ item_id, quantity }`: change a line's quantity (0 = remove).
      - `{ note }`: persist a customer note on the cart.
    Either field can be present; both are optional.
    """
    cart = await _get_cart_for(owner)
    changed = False

    if request.item_id is not None and request.quantity is not None:
        parts = request.item_id.split(":")
        try:
            product_id = UUID(parts[0])
            variant_id = UUID(parts[1]) if len(parts) > 1 else None
        except ValueError:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid item_id"
            )

        existing = cart.get_item(product_id, variant_id)
        if not existing:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Cart item not found",
            )

        # Cap the new quantity to the variant's available stock so the in-cart
        # +/- stepper can't push a line past inventory (the storefront stepper
        # is just UX; this is the authoritative guard).
        new_qty = request.quantity
        if new_qty > 0 and variant_id is not None:
            from src.infrastructure.database.connection import AsyncSessionLocal
            from src.infrastructure.repositories.variant_repository import (
                VariantRepository,
            )

            async with AsyncSessionLocal() as _s:
                variant = await VariantRepository(_s).get_by_id(variant_id)
            if variant is not None:
                new_qty = min(new_qty, variant.inventory_quantity)

        if new_qty <= 0:
            cart.remove_item(product_id, variant_id)
        else:
            # The Cart entity exposes `update_item_quantity(product_id,
            # quantity, variant_id?)` — there is NO `set_item_quantity`.
            # Calling the missing method raised AttributeError → 500 on every
            # quantity change (the cart +/- stepper). Note the arg order:
            # (product_id, quantity, variant_id), not (product_id, variant_id,
            # quantity).
            cart.update_item_quantity(product_id, new_qty, variant_id)
        changed = True

    if request.note is not None:
        # Cart entity should support a `note` attribute; assign through.
        # Tolerate models that don't have it yet by ignoring silently.
        if hasattr(cart, "note"):
            cart.note = request.note
            changed = True

    if changed:
        await _cart_repo.save(cart)
    return await _build_cart_response(cart, product_repo)


@router.post(
    "/cart/discount",
    response_model=SuccessResponse[CartResponse],
    summary="Apply discount code (SDK alias)",
    operation_id="sdk_apply_cart_discount",
)
async def sdk_apply_discount(
    request: SdkDiscountRequest,
    owner: Annotated[CartOwner, Depends(get_cart_owner)],
    product_repo: Annotated[ProductRepository, Depends(get_product_repository)],
    coupon_repo: Annotated[CouponRepository, Depends(get_coupon_repository)],
):
    """Validate a coupon code and pin it to the cart.

    The discount math runs at checkout (cart contents change in-flight,
    so the canonical computation is server-side at the order step). Here
    we just verify the code exists, is active for this store, and pin it
    so the checkout step can apply it.
    """
    cart = await _get_cart_for(owner)

    coupon = await coupon_repo.get_by_code(owner.store_id, request.code.strip().upper())
    if not coupon or not getattr(coupon, "is_active", False):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid or inactive coupon code",
        )

    if hasattr(cart, "discount_code"):
        cart.discount_code = coupon.code
        await _cart_repo.save(cart)
    return await _build_cart_response(cart, product_repo)


@router.delete(
    "/cart/discount",
    response_model=SuccessResponse[CartResponse],
    summary="Remove discount code (SDK alias)",
    operation_id="sdk_remove_cart_discount",
)
async def sdk_remove_discount(
    owner: Annotated[CartOwner, Depends(get_cart_owner)],
    product_repo: Annotated[ProductRepository, Depends(get_product_repository)],
):
    cart = await _get_cart_for(owner)
    if hasattr(cart, "discount_code") and cart.discount_code:
        cart.discount_code = None
        await _cart_repo.save(cart)
    return await _build_cart_response(cart, product_repo)


@router.post(
    "/cart/recover",
    response_model=SuccessResponse[CartResponse],
    summary="Rebuild the session cart from a saved abandoned cart",
    operation_id="sdk_recover_cart",
)
async def sdk_recover_cart(
    request: SdkRecoverCartRequest,
    owner: Annotated[CartOwner, Depends(get_cart_owner)],
    product_repo: Annotated[ProductRepository, Depends(get_product_repository)],
    admin_db: Annotated[AsyncSession, Depends(get_admin_db_session)],
):
    """Restore an abandoned cart's line items into the caller's session cart.

    Powers the WhatsApp / email abandoned-cart recovery link: the shopper
    (often on a fresh device with an empty cart) taps a link carrying
    ``recover_id`` and lands back on their cart with the items restored.

    Each line is re-validated against the CURRENT catalog — product +
    variant must still exist, be ACTIVE, belong to this store, and be in
    stock; price is snapshotted live. Anything that no longer qualifies is
    skipped rather than failing the whole restore. Existing items in the
    session cart are preserved (merge, not replace). ``get_cart_owner``
    establishes the ``numu_cart_session`` cookie so the rebuilt cart sticks
    to this browser.
    """
    line_items = await _resolve_recover_line_items(
        request.recover_id, owner.store_id, admin_db
    )
    cart = await _get_cart_for(owner)

    from src.infrastructure.database.connection import AsyncSessionLocal
    from src.infrastructure.repositories.variant_repository import VariantRepository

    for li in line_items:
        try:
            product_id = UUID(str(li.get("product_id")))
        except (ValueError, TypeError):
            continue
        raw_variant = li.get("variant_id")
        variant_id: UUID | None = None
        if raw_variant:
            try:
                variant_id = UUID(str(raw_variant))
            except (ValueError, TypeError):
                variant_id = None
        try:
            want_qty = int(li.get("quantity") or 1)
        except (ValueError, TypeError):
            want_qty = 1
        if want_qty <= 0:
            continue

        product = await product_repo.get_by_id(product_id)
        if (
            not product
            or product.status not in PURCHASABLE_STATUSES
            or product.store_id != owner.store_id
            or not product.is_in_stock
        ):
            continue  # dead / cross-store / OOS product — skip, don't fail

        unit_price_cents = product.effective_price().cents
        line_sku = product.sku
        line_image = product.images[0] if product.images else None
        variant_name: str | None = None
        add_qty = want_qty

        if variant_id:
            async with AsyncSessionLocal() as _s:
                variant = await VariantRepository(_s).get_by_id(variant_id)
            if (
                variant is None
                or variant.product_id != product.id
                or not product.variant_is_in_stock(variant)
            ):
                continue
            unit_price_cents = variant.price.cents
            line_sku = variant.sku or product.sku
            if variant.image_url:
                line_image = variant.image_url
            if variant.option_values:
                variant_name = " / ".join(
                    str(v) for v in variant.option_values.values() if v
                )
            # Cap to remaining stock net of anything already in the cart —
            # unless the merchant is deliberately overselling, in which case
            # there is no ceiling to cap against.
            existing = cart.get_item(product_id, variant_id)
            existing_qty = existing.quantity if existing else 0
            allowed = (
                want_qty
                if product.continue_selling_when_out_of_stock
                else max(0, variant.inventory_quantity - existing_qty)
            )
            if allowed <= 0:
                continue
            add_qty = min(want_qty, allowed)

        cart.add_item(
            CartItem(
                product_id=product_id,
                product_name=product.name,
                variant_id=variant_id,
                variant_name=variant_name,
                quantity=add_qty,
                unit_price=unit_price_cents,
                sku=line_sku,
                image_url=line_image,
            )
        )

    await _cart_repo.save(cart)
    return await _build_cart_response(cart, product_repo)
