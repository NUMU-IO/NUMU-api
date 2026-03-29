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
from src.api.dependencies.repositories import get_product_repository
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
from src.infrastructure.repositories import ProductRepository
from src.infrastructure.repositories.cart_repository import RedisCartRepository

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


async def _build_cart_response(
    cart: Cart,
    product_repo: ProductRepository,
) -> SuccessResponse[CartResponse]:
    """Build a CartResponse from a Cart entity, resolving live product data."""
    items: list[CartItemResponse] = []
    subtotal = 0

    for cart_item in cart.items:
        product = await product_repo.get_by_id(cart_item.product_id)
        if not product or product.status != ProductStatus.ACTIVE:
            continue
        unit_price = product.price.cents
        total_price = unit_price * cart_item.quantity
        subtotal += total_price
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
                image_url=product.images[0] if product.images else None,
                in_stock=product.is_in_stock,
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

    new_item = CartItem(
        product_id=request.product_id,
        product_name=product.name,
        variant_id=request.variant_id,
        quantity=request.quantity,
        unit_price=product.price.cents,
        sku=product.sku,
        image_url=product.images[0] if product.images else None,
    )
    cart.add_item(new_item)
    await _cart_repo.save(cart)

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
