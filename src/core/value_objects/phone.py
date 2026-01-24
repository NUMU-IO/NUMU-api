"""Phone number value object."""

from dataclasses import dataclass

import phonenumbers
from phonenumbers import NumberParseException


@dataclass(frozen=True)
class PhoneNumber:
    """Phone number value object with validation."""

    value: str
    country_code: str = "EG"

    def __post_init__(self) -> None:
        """Validate and normalize phone number if possible."""
        try:
            parsed = phonenumbers.parse(self.value, self.country_code)
            if phonenumbers.is_valid_number(parsed):
                # Store in E.164 format if valid
                formatted = phonenumbers.format_number(
                    parsed, phonenumbers.PhoneNumberFormat.E164
                )
                object.__setattr__(self, "value", formatted)
        except NumberParseException:
            # Keep original value if parsing fails
            pass

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
