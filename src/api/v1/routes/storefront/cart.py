"""Storefront cart routes.

URL: /storefront/me/cart

Server-side cart backed by Redis for persistence across restarts
and horizontal scaling.
"""

import logging
from typing import Annotated
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, HTTPException, Path, status

from src.api.dependencies.auth import get_current_customer
from src.api.dependencies.repositories import (
    get_funnel_event_repository,
    get_product_repository,
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
from src.core.entities.product import ProductStatus
from src.core.value_objects.cart_item import CartItem
from src.infrastructure.database.connection import get_tenant_id
from src.infrastructure.repositories import ProductRepository
from src.infrastructure.repositories.cart_repository import RedisCartRepository
from src.infrastructure.repositories.funnel_event_repository import (
    FunnelEventRepository,
)

logger = logging.getLogger(__name__)

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
    """
    items: list[CartItemResponse] = []
    subtotal = 0

    for cart_item in cart.items:
        product = await product_repo.get_by_id(cart_item.product_id)
        if not product or product.status != ProductStatus.ACTIVE:
            continue

        # Snapshot — what the customer agreed to pay when they added the
        # line. CartItem.unit_price is set at add-time in add_cart_item.
        unit_price = cart_item.unit_price
        total_price = unit_price * cart_item.quantity
        subtotal += total_price

        # Live deltas — the front-end uses these to render "price changed"
        # banners and to disable Checkout when any line is sold-out.
        current_price = product.price.cents
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
                in_stock=product.is_in_stock,
                available_now=available_now,
                sold_out_now=sold_out_now,
            )
        )

    currency = "EGP"
    if items and cart.items:
        product = await product_repo.get_by_id(cart.items[0].product_id)
        if product:
            currency = product.price.currency.value

    return SuccessResponse(
        data=CartResponse(
            items=items,
            item_count=len(items),
            total_quantity=sum(i.quantity for i in items),
            subtotal=subtotal,
            currency=currency,
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
    current_customer: Annotated[Customer, Depends(get_current_customer)],
    product_repo: Annotated[ProductRepository, Depends(get_product_repository)],
    funnel_repo: Annotated[FunnelEventRepository, Depends(get_funnel_event_repository)],
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
    if product.status != ProductStatus.ACTIVE:
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
    variant_price_cents = product.price.cents
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
        if not variant.is_in_stock:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="This variant is out of stock.",
            )
        variant_price_cents = int(variant.price.amount)
        variant_sku = variant.sku or product.sku
        if variant.image_url:
            variant_image = variant.image_url
        # Format the option_values dict as a human-readable label —
        # ordered by the product.options[].position so the rendering is
        # stable across stores ("Size: M / Color: Red", never
        # "Color: Red / Size: M"). Falls back to the alphabetical key
        # order when product.options is missing positions or empty.
        ov = variant.option_values or {}
        if ov:
            order = product.options or []
            if order:
                positioned = sorted(
                    order,
                    key=lambda a: (a.get("position", 0) or 0, a.get("name", "")),
                )
                keys = [a["name"] for a in positioned if a.get("name") in ov]
                # Any axis that's in option_values but missing from the
                # product.options metadata gets appended alphabetically
                # so we don't silently drop labels.
                keys += sorted(set(ov.keys()) - set(keys))
            else:
                keys = sorted(ov.keys())
            variant_name = " / ".join(f"{k}: {ov[k]}" for k in keys)

    new_item = CartItem(
        product_id=request.product_id,
        product_name=product.name,
        variant_id=request.variant_id,
        variant_name=variant_name,
        quantity=request.quantity,
        unit_price=variant_price_cents,
        sku=variant_sku,
        image_url=variant_image,
    )
    cart.add_item(new_item)
    await _cart_repo.save(cart)

    # Emit funnel event
    try:
        tid = get_tenant_id()
        await funnel_repo.create(
            tenant_id=UUID(tid) if tid else current_customer.store_id,
            store_id=current_customer.store_id,
            step="add_to_cart",
            customer_id=current_customer.id,
            step_data={
                "product_id": str(request.product_id),
                "product_name": product.name,
                "quantity": request.quantity,
                "unit_price": product.price.cents,
            },
        )
    except Exception:
        pass

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
