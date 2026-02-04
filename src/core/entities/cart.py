"""Cart entity representing a shopping cart."""

from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

from pydantic import ConfigDict, Field

from src.core.entities.base import BaseEntity
from src.core.value_objects.cart_item import CartItem


class Cart(BaseEntity):
    """Cart entity representing a shopping cart.

    Carts are session-based and can belong to either an authenticated
    customer or a guest session. Items are stored as CartItem value objects.
    """

    model_config = ConfigDict(
        validate_assignment=True,
        from_attributes=True,
        use_enum_values=False,
        populate_by_name=True,
    )

    session_id: str
    store_id: UUID
    customer_id: UUID | None = None
    items: list[CartItem] = Field(default_factory=list)
    currency: str = "USD"
    notes: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    expires_at: datetime | None = None

    @property
    def item_count(self) -> int:
        """Get total number of items in cart."""
        return sum(item.quantity for item in self.items)

    @property
    def unique_item_count(self) -> int:
        """Get number of unique items (product/variant combinations) in cart."""
        return len(self.items)

    @property
    def subtotal(self) -> int:
        """Calculate subtotal in cents (before taxes and shipping)."""
        return sum(item.total_price for item in self.items)

    @property
    def is_empty(self) -> bool:
        """Check if cart is empty."""
        return len(self.items) == 0

    def _find_item_index(self, product_id: UUID, variant_id: UUID | None = None) -> int:
        """Find index of item in cart.

        Args:
            product_id: The product UUID.
            variant_id: Optional variant UUID.

        Returns:
            Index of item if found, -1 otherwise.
        """
        target_key = f"{product_id}:{variant_id}" if variant_id else str(product_id)
        for i, item in enumerate(self.items):
            if item.item_key == target_key:
                return i
        return -1

    def get_item(self, product_id: UUID, variant_id: UUID | None = None) -> CartItem | None:
        """Get an item from the cart.

        Args:
            product_id: The product UUID.
            variant_id: Optional variant UUID.

        Returns:
            CartItem if found, None otherwise.
        """
        index = self._find_item_index(product_id, variant_id)
        if index >= 0:
            return self.items[index]
        return None

    def add_item(self, item: CartItem) -> "Cart":
        """Add an item to the cart.

        If the same product/variant already exists, quantity is increased.

        Args:
            item: The CartItem to add.

        Returns:
            Self for method chaining.
        """
        index = self._find_item_index(item.product_id, item.variant_id)

        if index >= 0:

            existing_item = self.items[index]
            new_quantity = existing_item.quantity + item.quantity
            updated_item = existing_item.with_quantity(new_quantity)
            self.items = [
                updated_item if i == index else it for i, it in enumerate(self.items)
            ]
        else:

            self.items = [*self.items, item]

        self.updated_at = datetime.now(UTC)
        return self

    def remove_item(self, product_id: UUID, variant_id: UUID | None = None) -> "Cart":
        """Remove an item from the cart completely.

        Args:
            product_id: The product UUID.
            variant_id: Optional variant UUID.

        Returns:
            Self for method chaining.
        """
        index = self._find_item_index(product_id, variant_id)
        if index >= 0:
            self.items = [item for i, item in enumerate(self.items) if i != index]
            self.updated_at = datetime.now(UTC)
        return self

    def update_item_quantity(
        self,
        product_id: UUID,
        quantity: int,
        variant_id: UUID | None = None,
    ) -> "Cart":
        """Update the quantity of an item in the cart.

        If quantity is 0 or less, the item is removed.

        Args:
            product_id: The product UUID.
            quantity: The new quantity.
            variant_id: Optional variant UUID.

        Returns:
            Self for method chaining.

        Raises:
            ValueError: If item not found in cart.
        """
        index = self._find_item_index(product_id, variant_id)

        if index < 0:
            raise ValueError(f"Item with product_id {product_id} not found in cart")

        if quantity <= 0:

            return self.remove_item(product_id, variant_id)

        existing_item = self.items[index]
        updated_item = existing_item.with_quantity(quantity)
        self.items = [
            updated_item if i == index else it for i, it in enumerate(self.items)
        ]
        self.updated_at = datetime.now(UTC)
        return self

    def clear(self) -> "Cart":
        """Remove all items from the cart.

        Returns:
            Self for method chaining.
        """
        self.items = []
        self.updated_at = datetime.now(UTC)
        return self

    def merge_cart(self, other: "Cart") -> "Cart":
        """Merge another cart into this one.

        Items from the other cart are added to this cart.
        If same product/variant exists, quantities are combined.

        Args:
            other: The cart to merge from.

        Returns:
            Self for method chaining.
        """
        for item in other.items:
            self.add_item(item)
        return self

    def to_dict(self) -> dict[str, Any]:
        """Convert cart to dictionary for serialization."""
        return {
            "id": str(self.id),
            "session_id": self.session_id,
            "store_id": str(self.store_id),
            "customer_id": str(self.customer_id) if self.customer_id else None,
            "items": [item.to_dict() for item in self.items],
            "currency": self.currency,
            "notes": self.notes,
            "metadata": self.metadata,
            "created_at": self.created_at.isoformat(),
            "updated_at": self.updated_at.isoformat(),
            "expires_at": self.expires_at.isoformat() if self.expires_at else None,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Cart":
        """Create Cart from dictionary."""
        return cls(
            id=UUID(data["id"]) if data.get("id") else uuid4(),
            session_id=data["session_id"],
            store_id=UUID(data["store_id"]),
            customer_id=UUID(data["customer_id"]) if data.get("customer_id") else None,
            items=[CartItem.from_dict(item) for item in data.get("items", [])],
            currency=data.get("currency", "USD"),
            notes=data.get("notes"),
            metadata=data.get("metadata", {}),
            created_at=datetime.fromisoformat(data["created_at"]) if data.get("created_at") else datetime.now(UTC),
            updated_at=datetime.fromisoformat(data["updated_at"]) if data.get("updated_at") else datetime.now(UTC),
            expires_at=datetime.fromisoformat(data["expires_at"]) if data.get("expires_at") else None,
        )
