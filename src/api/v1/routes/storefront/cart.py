"""Storefront cart routes.

URL: /storefront/me/cart

Server-side cart backed by an in-memory store keyed by customer ID.
In production, swap the storage backend to Redis for persistence
across restarts and horizontal scaling.
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
from src.core.entities.customer import Customer
from src.core.entities.product import ProductStatus
from src.infrastructure.repositories import ProductRepository

logger = logging.getLogger(__name__)

router = APIRouter()

# ---------------------------------------------------------------------------
# In-memory cart store  (keyed by customer UUID)
# Replace with Redis-backed implementation for production.
# ---------------------------------------------------------------------------
_carts: dict[UUID, list[dict]] = {}


def _get_cart(customer_id: UUID) -> list[dict]:
    """Return the mutable cart list for a customer, creating if needed."""
    if customer_id not in _carts:
        _carts[customer_id] = []
    return _carts[customer_id]


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@router.get(
    "/cart",
    response_model=SuccessResponse[CartResponse],
    summary="Get cart",
)
async def get_cart(
    current_customer: Annotated[Customer, Depends(get_current_customer)],
    product_repo: Annotated[ProductRepository, Depends(get_product_repository)],
):
    """Return the current customer's cart with live product data."""
    raw_items = _get_cart(current_customer.id)
    items: list[CartItemResponse] = []
    subtotal = 0

    for entry in raw_items:
        product = await product_repo.get_by_id(UUID(entry["product_id"]))
        if not product or product.status != ProductStatus.ACTIVE:
            continue
        unit_price = int(product.price.amount_cents)
        total_price = unit_price * entry["quantity"]
        subtotal += total_price
        items.append(
            CartItemResponse(
                id=entry["id"],
                product_id=str(product.id),
                product_name=product.name,
                variant_id=entry.get("variant_id"),
                variant_name=None,
                sku=product.sku,
                quantity=entry["quantity"],
                unit_price=unit_price,
                total_price=total_price,
                image_url=product.images[0] if product.images else None,
                in_stock=product.is_in_stock,
            )
        )

    currency = "EGP"
    if items:
        product = await product_repo.get_by_id(UUID(raw_items[0]["product_id"]))
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


@router.post(
    "/cart/items",
    response_model=SuccessResponse[CartResponse],
    status_code=status.HTTP_201_CREATED,
    summary="Add item to cart",
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

    cart = _get_cart(current_customer.id)
    variant_str = str(request.variant_id) if request.variant_id else None

    # Check for existing entry with same product + variant
    for entry in cart:
        if (
            entry["product_id"] == str(request.product_id)
            and entry.get("variant_id") == variant_str
        ):
            entry["quantity"] += request.quantity
            break
    else:
        cart.append({
            "id": str(uuid4()),
            "product_id": str(request.product_id),
            "variant_id": variant_str,
            "quantity": request.quantity,
        })

    # Return refreshed cart
    return await get_cart(current_customer, product_repo)


@router.patch(
    "/cart/items/{item_id}",
    response_model=SuccessResponse[CartResponse],
    summary="Update cart item quantity",
)
async def update_cart_item(
    item_id: Annotated[str, Path(description="Cart item ID")],
    request: UpdateCartItemRequest,
    current_customer: Annotated[Customer, Depends(get_current_customer)],
    product_repo: Annotated[ProductRepository, Depends(get_product_repository)],
):
    """Update the quantity of a cart item."""
    cart = _get_cart(current_customer.id)

    for entry in cart:
        if entry["id"] == item_id:
            entry["quantity"] = request.quantity
            return await get_cart(current_customer, product_repo)

    raise HTTPException(
        status_code=status.HTTP_404_NOT_FOUND,
        detail="Cart item not found",
    )


@router.delete(
    "/cart/items/{item_id}",
    response_model=SuccessResponse[CartResponse],
    summary="Remove item from cart",
)
async def remove_cart_item(
    item_id: Annotated[str, Path(description="Cart item ID")],
    current_customer: Annotated[Customer, Depends(get_current_customer)],
    product_repo: Annotated[ProductRepository, Depends(get_product_repository)],
):
    """Remove a single item from the cart."""
    cart = _get_cart(current_customer.id)
    original_len = len(cart)
    _carts[current_customer.id] = [e for e in cart if e["id"] != item_id]

    if len(_carts[current_customer.id]) == original_len:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Cart item not found",
        )

    return await get_cart(current_customer, product_repo)


@router.delete(
    "/cart",
    response_model=SuccessResponse[CartResponse],
    summary="Clear cart",
)
async def clear_cart(
    current_customer: Annotated[Customer, Depends(get_current_customer)],
    product_repo: Annotated[ProductRepository, Depends(get_product_repository)],
):
    """Remove all items from the customer's cart."""
    _carts[current_customer.id] = []
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
