"""Cart entity representing a customer's shopping cart."""

from uuid import UUID

from pydantic import Field

from src.core.entities.base import BaseEntity


class CartItem(BaseEntity):
    """Cart item entity representing a single item in a cart."""

    cart_id: UUID
    product_id: UUID
    quantity: int = Field(default=1, ge=1)
    variant_id: UUID | None = None

    def update_quantity(self, quantity: int) -> None:
        """Update item quantity.

        Args:
            quantity: New quantity (must be >= 1)

        Raises:
            ValueError: If quantity is less than 1
        """
        if quantity < 1:
            raise ValueError("Quantity must be at least 1")
        self.quantity = quantity
        self.touch()

    def increment(self, amount: int = 1) -> None:
        """Increment quantity by amount."""
        self.quantity += amount
        self.touch()


class Cart(BaseEntity):
    """Cart entity representing a customer's shopping cart.

    Each customer has one active cart per store. Carts are
    database-backed for session persistence.
    """

    store_id: UUID
    customer_id: UUID
    items: list[CartItem] = Field(default_factory=list)

    @property
    def item_count(self) -> int:
        """Get total number of items in the cart."""
        return sum(item.quantity for item in self.items)

    @property
    def is_empty(self) -> bool:
        """Check if cart is empty."""
        return len(self.items) == 0

    def find_item(self, product_id: UUID, variant_id: UUID | None = None) -> CartItem | None:
        """Find a cart item by product_id and variant_id."""
        for item in self.items:
            if item.product_id == product_id and item.variant_id == variant_id:
                return item
        return None

    def find_item_by_id(self, item_id: UUID) -> CartItem | None:
        """Find a cart item by its ID."""
        for item in self.items:
            if item.id == item_id:
                return item
        return None

    def add_item(self, product_id: UUID, quantity: int, variant_id: UUID | None = None) -> CartItem:
        """Add an item to the cart. Merges if same product+variant already exists.

        Returns:
            The added or updated CartItem.
        """
        existing = self.find_item(product_id, variant_id)
        if existing:
            existing.increment(quantity)
            self.touch()
            return existing

        item = CartItem(
            cart_id=self.id,
            product_id=product_id,
            quantity=quantity,
            variant_id=variant_id,
        )
        self.items.append(item)
        self.touch()
        return item

    def update_item(self, item_id: UUID, quantity: int) -> CartItem:
        """Update quantity of a cart item.

        Raises:
            ValueError: If item not found.
        """
        item = self.find_item_by_id(item_id)
        if not item:
            raise ValueError(f"Cart item with id {item_id} not found")
        item.update_quantity(quantity)
        self.touch()
        return item

    def remove_item(self, item_id: UUID) -> None:
        """Remove an item from the cart.

        Raises:
            ValueError: If item not found.
        """
        item = self.find_item_by_id(item_id)
        if not item:
            raise ValueError(f"Cart item with id {item_id} not found")
        self.items.remove(item)
        self.touch()

    def clear(self) -> None:
        """Remove all items from the cart."""
        self.items.clear()
        self.touch()
