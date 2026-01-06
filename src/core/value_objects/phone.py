"""Phone number value object."""

from dataclasses import dataclass

import phonenumbers
from phonenumbers import NumberParseException


@dataclass(frozen=True)
class PhoneNumber:
    """Phone number value object with validation."""

    value: str
    country_code: str = "US"

    def __post_init__(self) -> None:
        """Validate and normalize phone number."""
        try:
            parsed = phonenumbers.parse(self.value, self.country_code)
            if not phonenumbers.is_valid_number(parsed):
                raise ValueError(f"Invalid phone number: {self.value}")
            # Store in E.164 format
            formatted = phonenumbers.format_number(
                parsed, phonenumbers.PhoneNumberFormat.E164
            )
            object.__setattr__(self, "value", formatted)
        except NumberParseException as e:
            raise ValueError(f"Invalid phone number: {self.value}") from e

    def __str__(self) -> str:
        return self.value

    @property
    def national_format(self) -> str:
        """Get phone number in national format."""
        parsed = phonenumbers.parse(self.value)
        return phonenumbers.format_number(
            parsed, phonenumbers.PhoneNumberFormat.NATIONAL
        )

    @property
    def international_format(self) -> str:
        """Get phone number in international format."""
        parsed = phonenumbers.parse(self.value)
        return phonenumbers.format_number(
            parsed, phonenumbers.PhoneNumberFormat.INTERNATIONAL
        )
