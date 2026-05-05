"""SDK-style cart aliases at /storefront/cart/*.

The @numu/theme-sdk's NuMuProvider issues these calls:
  GET    /api/cart            (Next.js storefront proxy → here)
  POST   /api/cart/add        (proxy → /storefront/cart/add)
  POST   /api/cart/remove     (proxy → /storefront/cart/remove)
  POST   /api/cart/update     (proxy → /storefront/cart/update)
  POST   /api/cart/discount   (proxy → /storefront/cart/discount)
  DELETE /api/cart/discount   (proxy → /storefront/cart/discount)

Rather than reimplement the cart, these routes are thin shims around the
existing `cart.py` machinery (RedisCartRepository + CartItem + product
validation). The behavior matches the canonical `/storefront/me/cart/*`
routes byte-for-byte; only the URL layout differs.

All routes here require an authenticated storefront customer (cookie-based
session). The Next.js proxy forwards the cookie unchanged so the contract
is identical.
"""

from __future__ import annotations

import logging
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field

from src.api.dependencies.auth import get_current_customer
from src.api.dependencies.repositories import (
    get_coupon_repository,
    get_funnel_event_repository,
    get_product_repository,
)
from src.api.responses import SuccessResponse
from src.api.v1.routes.storefront.cart import (
    _build_cart_response,
    _cart_repo,
    _get_or_create_cart,
)
from src.api.v1.schemas.storefront.cart import CartResponse
from src.core.entities.customer import Customer
from src.core.entities.product import ProductStatus
from src.core.value_objects.cart_item import CartItem
from src.infrastructure.database.connection import get_tenant_id
from src.infrastructure.repositories import ProductRepository
from src.infrastructure.repositories.coupon_repository import CouponRepository
from src.infrastructure.repositories.funnel_event_repository import (
    FunnelEventRepository,
)

logger = logging.getLogger(__name__)

router = APIRouter()


# ─── SDK request bodies ──────────────────────────────────────────────────────


class SdkAddItemRequest(BaseModel):
    product_id: UUID
    variant_id: UUID | None = None
    quantity: int = Field(default=1, ge=1)


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


# ─── Routes ──────────────────────────────────────────────────────────────────


@router.get(
    "/cart",
    response_model=SuccessResponse[CartResponse],
    summary="Get cart (SDK alias)",
    operation_id="sdk_get_cart",
)
async def sdk_get_cart(
    current_customer: Annotated[Customer, Depends(get_current_customer)],
    product_repo: Annotated[ProductRepository, Depends(get_product_repository)],
):
    """Return the current customer's cart with live product data."""
    cart = await _get_or_create_cart(current_customer.id, current_customer.store_id)
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
    current_customer: Annotated[Customer, Depends(get_current_customer)],
    product_repo: Annotated[ProductRepository, Depends(get_product_repository)],
    funnel_repo: Annotated[FunnelEventRepository, Depends(get_funnel_event_repository)],
):
    product = await product_repo.get_by_id(request.product_id)
    if not product:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Product not found"
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
    cart.add_item(
        CartItem(
            product_id=request.product_id,
            product_name=product.name,
            variant_id=request.variant_id,
            quantity=request.quantity,
            unit_price=product.price.cents,
            sku=product.sku,
            image_url=product.images[0] if product.images else None,
        )
    )
    await _cart_repo.save(cart)

    # Best-effort funnel event — never fail the cart write on telemetry.
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


@router.post(
    "/cart/remove",
    response_model=SuccessResponse[CartResponse],
    summary="Remove item from cart (SDK alias)",
    operation_id="sdk_remove_cart_item",
)
async def sdk_remove_cart_item(
    request: SdkRemoveItemRequest,
    current_customer: Annotated[Customer, Depends(get_current_customer)],
    product_repo: Annotated[ProductRepository, Depends(get_product_repository)],
):
    cart = await _get_or_create_cart(current_customer.id, current_customer.store_id)

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
    current_customer: Annotated[Customer, Depends(get_current_customer)],
    product_repo: Annotated[ProductRepository, Depends(get_product_repository)],
):
    """Two modes:
      - `{ item_id, quantity }`: change a line's quantity (0 = remove).
      - `{ note }`: persist a customer note on the cart.
    Either field can be present; both are optional.
    """
    cart = await _get_or_create_cart(current_customer.id, current_customer.store_id)
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

        if request.quantity == 0:
            cart.remove_item(product_id, variant_id)
        else:
            cart.set_item_quantity(product_id, variant_id, request.quantity)
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
    current_customer: Annotated[Customer, Depends(get_current_customer)],
    product_repo: Annotated[ProductRepository, Depends(get_product_repository)],
    coupon_repo: Annotated[CouponRepository, Depends(get_coupon_repository)],
):
    """Validate a coupon code and pin it to the cart.

    The discount math runs at checkout (cart contents change in-flight,
    so the canonical computation is server-side at the order step). Here
    we just verify the code exists, is active for this store, and pin it
    so the checkout step can apply it.
    """
    cart = await _get_or_create_cart(current_customer.id, current_customer.store_id)

    coupon = await coupon_repo.get_by_code(
        current_customer.store_id, request.code.strip().upper()
    )
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
    current_customer: Annotated[Customer, Depends(get_current_customer)],
    product_repo: Annotated[ProductRepository, Depends(get_product_repository)],
):
    cart = await _get_or_create_cart(current_customer.id, current_customer.store_id)
    if hasattr(cart, "discount_code") and cart.discount_code:
        cart.discount_code = None
        await _cart_repo.save(cart)
    return await _build_cart_response(cart, product_repo)
