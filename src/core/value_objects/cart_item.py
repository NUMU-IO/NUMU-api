"""CartItem value object representing an item in a shopping cart."""

from decimal import Decimal
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator


class CartItem(BaseModel):
    """Cart item value object.

    Represents a single item in a shopping cart with quantity and pricing.
    Immutable to ensure cart integrity.
    """

    model_config = ConfigDict(frozen=True)

    product_id: UUID
    product_name: str
    variant_id: UUID | None = None
    variant_name: str | None = None
    sku: str | None = None
    quantity: int = Field(default=1, ge=1)
    unit_price: int = Field(default=0, ge=0) 
    image_url: str | None = None
    weight: Decimal | None = None
    properties: dict[str, Any] = Field(default_factory=dict)

    @field_validator("quantity")
    @classmethod
    def validate_quantity(cls, v: int) -> int:
        """Validate that quantity is at least 1."""
        if v < 1:
            raise ValueError("Quantity must be at least 1")
        return v

    @field_validator("unit_price")
    @classmethod
    def validate_unit_price(cls, v: int) -> int:
        """Validate that unit price is non-negative."""
        if v < 0:
            raise ValueError("Unit price cannot be negative")
        return v

    @property
    def total_price(self) -> int:
        """Calculate total price for this item (in cents)."""
        return self.unit_price * self.quantity

    @property
    def item_key(self) -> str:
        """Generate unique key for this item (product + variant combination)."""
        if self.variant_id:
            return f"{self.product_id}:{self.variant_id}"
        return str(self.product_id)

    def with_quantity(self, new_quantity: int) -> "CartItem":
        """Create a new CartItem with updated quantity.

        Args:
            new_quantity: The new quantity for the item.

        Returns:
            A new CartItem instance with the updated quantity.
        """
        return CartItem(
            product_id=self.product_id,
            product_name=self.product_name,
            variant_id=self.variant_id,
            variant_name=self.variant_name,
            sku=self.sku,
            quantity=new_quantity,
            unit_price=self.unit_price,
            image_url=self.image_url,
            weight=self.weight,
            properties=self.properties,
        )

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for serialization."""
        return {
            "product_id": str(self.product_id),
            "product_name": self.product_name,
            "variant_id": str(self.variant_id) if self.variant_id else None,
            "variant_name": self.variant_name,
            "sku": self.sku,
            "quantity": self.quantity,
            "unit_price": self.unit_price,
            "image_url": self.image_url,
            "weight": str(self.weight) if self.weight else None,
            "properties": self.properties,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "CartItem":
        """Create CartItem from dictionary."""
        return cls(
            product_id=UUID(data["product_id"]),
            product_name=data["product_name"],
            variant_id=UUID(data["variant_id"]) if data.get("variant_id") else None,
            variant_name=data.get("variant_name"),
            sku=data.get("sku"),
            quantity=data.get("quantity", 1),
            unit_price=data.get("unit_price", 0),
            image_url=data.get("image_url"),
            weight=Decimal(data["weight"]) if data.get("weight") else None,
            properties=data.get("properties", {}),
        )
