"""Customer address entity for address book management."""

from datetime import datetime
from enum import Enum
from uuid import UUID

from src.core.entities.base import BaseEntity


class AddressLabel(str, Enum):
    """Address label enumeration."""

    HOME = "home"
    WORK = "work"
    OTHER = "other"


class CustomerAddress(BaseEntity):
    """Customer address entity for address book."""

    def __init__(
        self,
        customer_id: UUID,
        first_name: str,
        last_name: str,
        address_line1: str,
        city: str,
        country: str,
        address_line2: str | None = None,
        state: str | None = None,
        postal_code: str | None = None,
        phone: str | None = None,
        is_default: bool = False,
        label: AddressLabel = AddressLabel.HOME,
        id: UUID | None = None,
        created_at: datetime | None = None,
        updated_at: datetime | None = None,
    ) -> None:
        super().__init__(id=id, created_at=created_at, updated_at=updated_at)
        self.customer_id = customer_id
        self.first_name = first_name
        self.last_name = last_name
        self.address_line1 = address_line1
        self.address_line2 = address_line2
        self.city = city
        self.state = state
        self.postal_code = postal_code
        self.country = country
        self.phone = phone
        self.is_default = is_default
        self.label = label

    @property
    def full_name(self) -> str:
        """Get full name for the address."""
        return f"{self.first_name} {self.last_name}"

    @property
    def formatted_address(self) -> str:
        """Get formatted address string."""
        parts = [self.address_line1]
        if self.address_line2:
            parts.append(self.address_line2)
        parts.append(self.city)
        if self.state:
            parts.append(self.state)
        if self.postal_code:
            parts.append(self.postal_code)
        parts.append(self.country)
        return ", ".join(parts)

    def set_as_default(self) -> None:
        """Set this address as default."""
        self.is_default = True
        self.updated_at = datetime.utcnow()

    def unset_default(self) -> None:
        """Unset this address as default."""
        self.is_default = False
        self.updated_at = datetime.utcnow()
