"""Cart API schemas for storefront."""

from uuid import UUID

from pydantic import BaseModel, Field


class AddToCartRequest(BaseModel):
    """Request to add an item to the cart."""

    product_id: UUID
    quantity: int = Field(default=1, ge=1, description="Quantity to add")
    variant_id: UUID | None = None


class UpdateCartItemRequest(BaseModel):
    """Request to update a cart item's quantity."""

    quantity: int = Field(..., ge=1, description="New quantity")


class CartItemResponse(BaseModel):
    """Response for a single cart item."""

    id: str
    cart_id: str
    product_id: str
    quantity: int
    variant_id: str | None
    product_name: str | None
    product_price: int | None
    product_image: str | None
    in_stock: bool
    created_at: str | None
    updated_at: str | None

    class Config:
        from_attributes = True


class CartResponse(BaseModel):
    """Response for a cart."""

    id: str
    store_id: str
    customer_id: str
    items: list[CartItemResponse]
    item_count: int
    subtotal: int
    created_at: str | None
    updated_at: str | None

    class Config:
        from_attributes = True
