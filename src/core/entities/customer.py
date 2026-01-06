"""Customer entity representing a store's customer."""

from datetime import datetime
from uuid import UUID

from src.core.entities.base import BaseEntity
from src.core.value_objects.email import Email
from src.core.value_objects.phone import PhoneNumber


class Customer(BaseEntity):
    """Customer entity representing a store's customer."""

    def __init__(
        self,
        store_id: UUID,
        email: Email,
        first_name: str,
        last_name: str,
        phone: PhoneNumber | None = None,
        user_id: UUID | None = None,
        accepts_marketing: bool = False,
        notes: str | None = None,
        tags: list[str] | None = None,
        default_address_id: UUID | None = None,
        total_orders: int = 0,
        total_spent: int = 0,  # In cents
        metadata: dict | None = None,
        id: UUID | None = None,
        created_at: datetime | None = None,
        updated_at: datetime | None = None,
    ) -> None:
        super().__init__(id=id, created_at=created_at, updated_at=updated_at)
        self.store_id = store_id
        self.email = email
        self.first_name = first_name
        self.last_name = last_name
        self.phone = phone
        self.user_id = user_id
        self.accepts_marketing = accepts_marketing
        self.notes = notes
        self.tags = tags or []
        self.default_address_id = default_address_id
        self.total_orders = total_orders
        self.total_spent = total_spent
        self.metadata = metadata or {}

    @property
    def full_name(self) -> str:
        """Get customer's full name."""
        return f"{self.first_name} {self.last_name}"

    def record_order(self, order_total: int) -> None:
        """Record a new order for this customer."""
        self.total_orders += 1
        self.total_spent += order_total
        self.updated_at = datetime.utcnow()
