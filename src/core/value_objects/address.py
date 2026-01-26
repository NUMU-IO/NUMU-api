"""Address value object."""

from typing import Any

from pydantic import BaseModel, ConfigDict


class Address(BaseModel):
    """Address value object."""

    model_config = ConfigDict(frozen=True)

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

    def __hash__(self) -> int:
        return hash(
            (
                self.address_line1,
                self.address_line2,
                self.city,
                self.state,
                self.postal_code,
                self.country,
            )
        )

    def to_dict(self) -> dict[str, Any]:
        """Convert address to dictionary.

        Note: Prefer using model_dump() instead. This method is kept for
        backward compatibility.
        """
        return self.model_dump()

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Address":
        """Create address from dictionary.

        Note: Prefer using model_validate() instead. This method is kept for
        backward compatibility.
        """
        return cls.model_validate(data)

    @property
    def formatted_single_line(self) -> str:
        """Get address formatted as a single line."""
        return str(self)

    @property
    def formatted_multi_line(self) -> str:
        """Get address formatted as multiple lines."""
        lines = [self.address_line1]
        if self.address_line2:
            lines.append(self.address_line2)
        city_line = self.city
        if self.state:
            city_line = f"{city_line}, {self.state}"
        if self.postal_code:
            city_line = f"{city_line} {self.postal_code}"
        lines.append(city_line)
        lines.append(self.country)
        return "\n".join(lines)
