"""Address value object."""

from dataclasses import dataclass


@dataclass(frozen=True)
class Address:
    """Address value object."""

    address_line1: str
    city: str
    country: str
    address_line2: str | None = None
    state: str | None = None
    postal_code: str | None = None

    def __str__(self) -> str:
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

    def to_dict(self) -> dict:
        """Convert address to dictionary."""
        return {
            "address_line1": self.address_line1,
            "address_line2": self.address_line2,
            "city": self.city,
            "state": self.state,
            "postal_code": self.postal_code,
            "country": self.country,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "Address":
        """Create address from dictionary."""
        return cls(
            address_line1=data["address_line1"],
            address_line2=data.get("address_line2"),
            city=data["city"],
            state=data.get("state"),
            postal_code=data.get("postal_code"),
            country=data["country"],
        )
