"""Email value object."""

import re
from dataclasses import dataclass


@dataclass(frozen=True)
class Email:
    """Email value object with validation."""

    value: str

    def __post_init__(self) -> None:
        """Validate email format."""
        if not self._is_valid_email(self.value):
            raise ValueError(f"Invalid email address: {self.value}")
        # Normalize email to lowercase
        object.__setattr__(self, "value", self.value.lower().strip())

    @staticmethod
    def _is_valid_email(email: str) -> bool:
        """Check if email format is valid."""
        pattern = r"^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$"
        return bool(re.match(pattern, email))

    def __str__(self) -> str:
        return self.value

    def __eq__(self, other: object) -> bool:
        if isinstance(other, Email):
            return self.value == other.value
        if isinstance(other, str):
            return self.value == other.lower()
        return False

    def __hash__(self) -> int:
        return hash(self.value)
