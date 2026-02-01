"""Email value object."""

import re
from typing import Any

from pydantic import BaseModel, ConfigDict, field_validator


class Email(BaseModel):
    """Email value object with validation."""

    model_config = ConfigDict(frozen=True)

    value: str

    @field_validator("value", mode="before")
    @classmethod
    def validate_and_normalize(cls, v: Any) -> str:
        """Validate email format and normalize to lowercase."""
        if not isinstance(v, str):
            v = str(v)

        v = v.lower().strip()

        if not cls._is_valid_email(v):
            raise ValueError(f"Invalid email address: {v}")

        return v

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
