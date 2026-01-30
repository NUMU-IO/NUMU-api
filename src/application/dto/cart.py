"""Cart DTOs."""

from dataclasses import dataclass, field
from datetime import datetime
from uuid import UUID

from src.application.dto.base import BaseDTO
from src.core.entities.cart import Cart, CartItem


@dataclass
class CartItemDTO(BaseDTO):
    """Cart item data transfer object."""

    id: UUID
    cart_id: UUID
    product_id: UUID
    quantity: int
    variant_id: UUID | None
    # Resolved product info (populated at read time)
    product_name: str | None = None
    product_price: int | None = None  # In cents
    product_image: str | None = None
    in_stock: bool = True
    created_at: datetime | None = None
    updated_at: datetime | None = None

    @classmethod
    def from_entity(cls, item: CartItem) -> "CartItemDTO":
        """Create DTO from CartItem entity (without product details)."""
        return cls(
            id=item.id,
            cart_id=item.cart_id,
            product_id=item.product_id,
            quantity=item.quantity,
            variant_id=item.variant_id,
            created_at=item.created_at,
            updated_at=item.updated_at,
        )


@dataclass
class CartDTO(BaseDTO):
    """Cart data transfer object."""

    id: UUID
    store_id: UUID
    customer_id: UUID
    items: list[CartItemDTO]
    item_count: int
    subtotal: int  # In cents, calculated from resolved product prices
    created_at: datetime | None = None
    updated_at: datetime | None = None

    @classmethod
    def from_entity(cls, entity: Cart, subtotal: int = 0) -> "CartDTO":
        """Create DTO from Cart entity."""
        return cls(
            id=entity.id,
            store_id=entity.store_id,
            customer_id=entity.customer_id,
            items=[CartItemDTO.from_entity(item) for item in entity.items],
            item_count=entity.item_count,
            subtotal=subtotal,
            created_at=entity.created_at,
            updated_at=entity.updated_at,
        )


@dataclass
class AddToCartDTO(BaseDTO):
    """Add to cart data transfer object."""

    product_id: UUID
    quantity: int = 1
    variant_id: UUID | None = None


@dataclass
class UpdateCartItemDTO(BaseDTO):
    """Update cart item data transfer object."""

    quantity: int
