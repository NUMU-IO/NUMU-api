"""Customer address entity for address book management."""

from enum import StrEnum
from uuid import UUID

from src.core.entities.base import BaseEntity


class AddressLabel(StrEnum):
    """Address label enumeration."""

    HOME = "home"
    WORK = "work"
    OTHER = "other"


class CustomerAddress(BaseEntity):
    """Customer address entity for address book.

    Addresses are owned by customers and can be labeled (home, work, etc.).
    One address per customer can be marked as the default.
    """

    customer_id: UUID
    first_name: str
    last_name: str
    address_line1: str
    city: str
    country: str
    address_line2: str | None = None
    state: str | None = None
    postal_code: str | None = None
    phone: str | None = None
    is_default: bool = False
    label: AddressLabel = AddressLabel.HOME

    @property
    def full_name(self) -> str:
        """Get full name for the address."""
        return f"{self.first_name} {self.last_name}"

    @property
    def formatted_address(self) -> str:
        """Get formatted address as a single line."""
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

    @property
    def formatted_multiline(self) -> str:
        """Get formatted address as multiple lines."""
        lines = [f"{self.first_name} {self.last_name}"]
        lines.append(self.address_line1)
        if self.address_line2:
            lines.append(self.address_line2)
        city_line = self.city
        if self.state:
            city_line = f"{city_line}, {self.state}"
        if self.postal_code:
            city_line = f"{city_line} {self.postal_code}"
        lines.append(city_line)
        lines.append(self.country)
        if self.phone:
            lines.append(f"Phone: {self.phone}")
        return "\n".join(lines)

    def set_as_default(self) -> None:
        """Set this address as the default."""
        self.is_default = True
        self.touch()

    def unset_default(self) -> None:
        """Unset this address as the default."""
        self.is_default = False
        self.touch()

    def update_label(self, label: AddressLabel) -> None:
        """Update the address label.

        Args:
            label: New address label
        """
        self.label = label
        self.touch()

    def is_complete(self) -> bool:
        """Check if address has all required fields."""
        return all([
            self.first_name,
            self.last_name,
            self.address_line1,
            self.city,
            self.country,
        ])
