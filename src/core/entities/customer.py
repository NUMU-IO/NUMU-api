"""Customer entity representing a store's customer."""

from typing import Any
from uuid import UUID

from pydantic import Field

from src.core.entities.base import BaseEntity
from src.core.value_objects.email import Email
from src.core.value_objects.phone import PhoneNumber


class Customer(BaseEntity):
    """Customer entity representing a store's customer.

    Customers are scoped to individual stores (multi-tenant).
    They can optionally be linked to a User account for SSO.
    """

    store_id: UUID
    email: Email
    first_name: str
    last_name: str
    phone: PhoneNumber | None = None
    password_hash: str | None = None
    user_id: UUID | None = None
    accepts_marketing: bool = False
    notes: str | None = None
    tags: list[str] = Field(default_factory=list)
    default_address_id: UUID | None = None
    total_orders: int = Field(default=0, ge=0)
    total_spent: int = Field(default=0, ge=0)  # In cents
    is_verified: bool = False
    metadata: dict[str, Any] = Field(default_factory=dict)

    @property
    def full_name(self) -> str:
        """Get customer's full name."""
        return f"{self.first_name} {self.last_name}"

    @property
    def has_account(self) -> bool:
        """Check if customer has a password set (can log in)."""
        return self.password_hash is not None

    @property
    def is_linked_to_user(self) -> bool:
        """Check if customer is linked to a user account."""
        return self.user_id is not None

    @property
    def average_order_value(self) -> float:
        """Calculate average order value in dollars."""
        if self.total_orders == 0:
            return 0.0
        return self.total_spent / self.total_orders / 100

    def record_order(self, order_total: int) -> None:
        """Record a new order for this customer.

        Args:
            order_total: Order total in cents
        """
        self.total_orders += 1
        self.total_spent += order_total
        self.touch()

    def update_password(self, password_hash: str) -> None:
        """Update customer password hash.

        Args:
            password_hash: New bcrypt password hash
        """
        self.password_hash = password_hash
        self.touch()

    def verify(self) -> None:
        """Mark customer email as verified."""
        self.is_verified = True
        self.touch()

    def set_default_address(self, address_id: UUID) -> None:
        """Set the default shipping address.

        Args:
            address_id: The address ID to set as default
        """
        self.default_address_id = address_id
        self.touch()

    def clear_default_address(self) -> None:
        """Clear the default shipping address."""
        self.default_address_id = None
        self.touch()

    def add_tag(self, tag: str) -> None:
        """Add a tag to the customer.

        Args:
            tag: Tag to add
        """
        normalized_tag = tag.lower().strip()
        if normalized_tag and normalized_tag not in self.tags:
            self.tags.append(normalized_tag)
            self.touch()

    def remove_tag(self, tag: str) -> None:
        """Remove a tag from the customer.

        Args:
            tag: Tag to remove
        """
        normalized_tag = tag.lower().strip()
        if normalized_tag in self.tags:
            self.tags.remove(normalized_tag)
            self.touch()

    def opt_in_marketing(self) -> None:
        """Opt in to marketing communications."""
        self.accepts_marketing = True
        self.touch()

    def opt_out_marketing(self) -> None:
        """Opt out of marketing communications."""
        self.accepts_marketing = False
        self.touch()

    def link_to_user(self, user_id: UUID) -> None:
        """Link this customer to a user account.

        Args:
            user_id: The user ID to link
        """
        self.user_id = user_id
        self.touch()

    def unlink_from_user(self) -> None:
        """Unlink this customer from its user account."""
        self.user_id = None
        self.touch()

    def update_notes(self, notes: str | None) -> None:
        """Update customer notes.

        Args:
            notes: New notes text
        """
        self.notes = notes
        self.touch()
