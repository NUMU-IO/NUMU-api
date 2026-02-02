"""Cart Pydantic schemas for storefront."""

from uuid import UUID

from pydantic import BaseModel, Field


class AddCartItemRequest(BaseModel):
    """Add item to cart request schema."""

    product_id: UUID
    variant_id: UUID | None = None
    quantity: int = Field(default=1, ge=1, le=999)


class UpdateCartItemRequest(BaseModel):
    """Update cart item quantity request schema."""

    quantity: int = Field(..., ge=1, le=999)


class CartItemResponse(BaseModel):
    """Cart item response schema."""

    id: str
    product_id: str
    product_name: str
    variant_id: str | None = None
    variant_name: str | None = None
    sku: str | None = None
    quantity: int
    unit_price: int = Field(description="Price in cents")
    total_price: int = Field(description="quantity * unit_price in cents")
    image_url: str | None = None
    in_stock: bool = True

    class Config:
        from_attributes = True


class CartResponse(BaseModel):
    """Full cart response schema."""

    items: list[CartItemResponse]
    item_count: int = Field(description="Total number of distinct line items")
    total_quantity: int = Field(description="Sum of all item quantities")
    subtotal: int = Field(description="Subtotal in cents")
    currency: str = "EGP"

    class Config:
        from_attributes = True
