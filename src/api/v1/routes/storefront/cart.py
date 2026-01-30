"""Cart routes for authenticated customers.

URL: /storefront/me/cart/...

These routes require customer authentication and provide:
- View cart
- Add item to cart
- Update item quantity
- Remove item from cart
- Clear cart
"""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Path, status

from src.api.dependencies.auth import get_current_customer
from src.api.dependencies.repositories import (
    get_cart_repository,
    get_product_repository,
    get_store_repository,
)
from src.api.responses import SuccessResponse
from src.api.v1.schemas.storefront.cart import (
    AddToCartRequest,
    CartItemResponse,
    CartResponse,
    UpdateCartItemRequest,
)
from src.application.dto.cart import AddToCartDTO, UpdateCartItemDTO
from src.application.use_cases.storefront.cart import (
    AddToCartUseCase,
    ClearCartUseCase,
    GetCartUseCase,
    RemoveCartItemUseCase,
    UpdateCartItemUseCase,
)
from src.core.entities.customer import Customer
from src.core.exceptions import EntityNotFoundError
from src.infrastructure.repositories import (
    CartRepository,
    ProductRepository,
    StoreRepository,
)

router = APIRouter()


async def get_customer_tenant_id(
    current_customer: Annotated[Customer, Depends(get_current_customer)],
    store_repo: Annotated[StoreRepository, Depends(get_store_repository)],
) -> UUID:
    """Resolve tenant_id from the customer's store. Executed once per request."""
    store = await store_repo.get_by_id(current_customer.store_id)
    if not store:
        raise EntityNotFoundError("Store", str(current_customer.store_id))
    return getattr(store, "tenant_id", store.id)


def _cart_dto_to_response(dto) -> CartResponse:
    """Convert CartDTO to CartResponse."""
    return CartResponse(
        id=str(dto.id),
        store_id=str(dto.store_id),
        customer_id=str(dto.customer_id),
        items=[
            CartItemResponse(
                id=str(item.id),
                cart_id=str(item.cart_id),
                product_id=str(item.product_id),
                quantity=item.quantity,
                variant_id=str(item.variant_id) if item.variant_id else None,
                product_name=item.product_name,
                product_price=item.product_price,
                product_image=item.product_image,
                in_stock=item.in_stock,
                created_at=str(item.created_at) if item.created_at else None,
                updated_at=str(item.updated_at) if item.updated_at else None,
            )
            for item in dto.items
        ],
        item_count=dto.item_count,
        subtotal=dto.subtotal,
        created_at=str(dto.created_at) if dto.created_at else None,
        updated_at=str(dto.updated_at) if dto.updated_at else None,
    )


@router.get(
    "/cart",
    response_model=SuccessResponse[CartResponse],
    summary="Get current cart",
)
async def get_cart(
    current_customer: Annotated[Customer, Depends(get_current_customer)],
    tenant_id: Annotated[UUID, Depends(get_customer_tenant_id)],
    cart_repo: Annotated[CartRepository, Depends(get_cart_repository)],
    product_repo: Annotated[ProductRepository, Depends(get_product_repository)],
):
    """Get the current customer's cart with resolved product details."""
    use_case = GetCartUseCase(
        cart_repository=cart_repo,
        product_repository=product_repo,
    )
    result = await use_case.execute(
        store_id=current_customer.store_id,
        customer_id=current_customer.id,
        tenant_id=tenant_id,
    )

    return SuccessResponse(
        data=_cart_dto_to_response(result),
        message="Cart retrieved successfully",
    )


@router.post(
    "/cart/items",
    response_model=SuccessResponse[CartResponse],
    status_code=status.HTTP_201_CREATED,
    summary="Add item to cart",
)
async def add_to_cart(
    request: AddToCartRequest,
    current_customer: Annotated[Customer, Depends(get_current_customer)],
    tenant_id: Annotated[UUID, Depends(get_customer_tenant_id)],
    cart_repo: Annotated[CartRepository, Depends(get_cart_repository)],
    product_repo: Annotated[ProductRepository, Depends(get_product_repository)],
):
    """Add an item to the cart. Merges quantity if product already exists."""

    use_case = AddToCartUseCase(
        cart_repository=cart_repo,
        product_repository=product_repo,
    )
    dto = AddToCartDTO(
        product_id=request.product_id,
        quantity=request.quantity,
        variant_id=request.variant_id,
    )
    result = await use_case.execute(
        store_id=current_customer.store_id,
        customer_id=current_customer.id,
        tenant_id=tenant_id,
        dto=dto,
    )

    return SuccessResponse(
        data=_cart_dto_to_response(result),
        message="Item added to cart successfully",
    )


@router.patch(
    "/cart/items/{item_id}",
    response_model=SuccessResponse[CartResponse],
    summary="Update cart item quantity",
)
async def update_cart_item(
    item_id: Annotated[UUID, Path(description="Cart item ID")],
    request: UpdateCartItemRequest,
    current_customer: Annotated[Customer, Depends(get_current_customer)],
    tenant_id: Annotated[UUID, Depends(get_customer_tenant_id)],
    cart_repo: Annotated[CartRepository, Depends(get_cart_repository)],
    product_repo: Annotated[ProductRepository, Depends(get_product_repository)],
):
    """Update the quantity of an item in the cart."""

    use_case = UpdateCartItemUseCase(
        cart_repository=cart_repo,
        product_repository=product_repo,
    )
    dto = UpdateCartItemDTO(quantity=request.quantity)
    result = await use_case.execute(
        store_id=current_customer.store_id,
        customer_id=current_customer.id,
        tenant_id=tenant_id,
        item_id=item_id,
        dto=dto,
    )

    return SuccessResponse(
        data=_cart_dto_to_response(result),
        message="Cart item updated successfully",
    )


@router.delete(
    "/cart/items/{item_id}",
    response_model=SuccessResponse[CartResponse],
    summary="Remove item from cart",
)
async def remove_cart_item(
    item_id: Annotated[UUID, Path(description="Cart item ID")],
    current_customer: Annotated[Customer, Depends(get_current_customer)],
    tenant_id: Annotated[UUID, Depends(get_customer_tenant_id)],
    cart_repo: Annotated[CartRepository, Depends(get_cart_repository)],
    product_repo: Annotated[ProductRepository, Depends(get_product_repository)],
):
    """Remove an item from the cart."""

    use_case = RemoveCartItemUseCase(
        cart_repository=cart_repo,
        product_repository=product_repo,
    )
    result = await use_case.execute(
        store_id=current_customer.store_id,
        customer_id=current_customer.id,
        tenant_id=tenant_id,
        item_id=item_id,
    )

    return SuccessResponse(
        data=_cart_dto_to_response(result),
        message="Item removed from cart successfully",
    )


@router.delete(
    "/cart",
    response_model=SuccessResponse[dict],
    summary="Clear cart",
)
async def clear_cart(
    current_customer: Annotated[Customer, Depends(get_current_customer)],
    cart_repo: Annotated[CartRepository, Depends(get_cart_repository)],
):
    """Remove all items from the cart."""
    use_case = ClearCartUseCase(cart_repository=cart_repo)
    await use_case.execute(
        store_id=current_customer.store_id,
        customer_id=current_customer.id,
    )

    return SuccessResponse(
        data={"success": True},
        message="Cart cleared successfully",
    )
