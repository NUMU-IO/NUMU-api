"""Cart DTOs."""

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

from src.application.dto.base import BaseDTO
from src.core.entities.cart import Cart
from src.core.value_objects.cart_item import CartItem


@dataclass
class CartItemDTO(BaseDTO):
    """Cart item data transfer object."""

    product_id: UUID
    product_name: str
    variant_id: UUID | None
    variant_name: str | None
    sku: str | None
    quantity: int
    unit_price: int
    total_price: int
    image_url: str | None = None
    weight: Decimal | None = None
    properties: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_entity(cls, item: CartItem) -> "CartItemDTO":
        """Create DTO from CartItem."""
        return cls(
            product_id=item.product_id,
            product_name=item.product_name,
            variant_id=item.variant_id,
            variant_name=item.variant_name,
            sku=item.sku,
            quantity=item.quantity,
            unit_price=item.unit_price,
            total_price=item.total_price,
            image_url=item.image_url,
            weight=item.weight,
            properties=item.properties,
        )


@dataclass
class CartDTO(BaseDTO):
    """Cart data transfer object."""

    id: UUID
    session_id: str
    store_id: UUID
    customer_id: UUID | None
    items: list[CartItemDTO]
    item_count: int
    unique_item_count: int
    subtotal: int
    currency: str
    notes: str | None
    created_at: datetime
    updated_at: datetime
    expires_at: datetime | None = None

    @classmethod
    def from_entity(cls, cart: Cart) -> "CartDTO":
        """Create DTO from Cart entity."""
        return cls(
            id=cart.id,
            session_id=cart.session_id,
            store_id=cart.store_id,
            customer_id=cart.customer_id,
            items=[CartItemDTO.from_entity(item) for item in cart.items],
            item_count=cart.item_count,
            unique_item_count=cart.unique_item_count,
            subtotal=cart.subtotal,
            currency=cart.currency,
            notes=cart.notes,
            created_at=cart.created_at,
            updated_at=cart.updated_at,
            expires_at=cart.expires_at,
        )


@dataclass
class AddToCartDTO(BaseDTO):
    """Add to cart request DTO."""

    product_id: UUID
    quantity: int = 1
    variant_id: UUID | None = None


@dataclass
class UpdateCartItemDTO(BaseDTO):
    """Update cart item request DTO."""

    product_id: UUID
    quantity: int
    variant_id: UUID | None = None


@dataclass
class RemoveFromCartDTO(BaseDTO):
    """Remove from cart request DTO."""

    product_id: UUID
    variant_id: UUID | None = None


@dataclass
class CartOperationResultDTO(BaseDTO):
    """Result of a cart operation."""

    success: bool
    cart: CartDTO
    message: str | None = None
