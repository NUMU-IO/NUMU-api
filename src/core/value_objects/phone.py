"""Phone number value object."""

from typing import Any, Self

import phonenumbers
from phonenumbers import NumberParseException
from pydantic import BaseModel, ConfigDict, model_validator


class PhoneNumber(BaseModel):
    """Phone number value object with validation."""

    model_config = ConfigDict(frozen=True)

    value: str
    country_code: str = "EG"

    @model_validator(mode="before")
    @classmethod
    def accept_bare_string(cls, data: Any) -> Any:
        """Accept a bare phone string in addition to the full
        ``{"value": ..., "country_code": ...}`` shape.

        Motivation: the CSV order-import service and several legacy
        callers pass phone as a raw string. Before this validator they
        hit a ``model_type`` validation error ("Input should be a valid
        dictionary or instance of PhoneNumber"). Normalising here lets
        every call site stay simple without forcing them all to wrap
        with ``PhoneNumber(value=...)``.

        Non-strings (dicts, PhoneNumber instances, None) pass through
        untouched so the existing ``value=...`` call sites are a no-op.
        """
        if isinstance(data, str):
            return {"value": data}
        return data

    @model_validator(mode="after")
    def normalize_phone(self) -> Self:
        """Validate and normalize phone number if possible."""
        try:
            parsed = phonenumbers.parse(self.value, self.country_code)
            if phonenumbers.is_valid_number(parsed):
                # Store in E.164 format if valid
                formatted = phonenumbers.format_number(
                    parsed, phonenumbers.PhoneNumberFormat.E164
                )
                # Use object.__setattr__ since frozen
                object.__setattr__(self, "value", formatted)
        except NumberParseException:
            # Keep original value if parsing fails
            pass
        return self

    def __str__(self) -> str:
        return self.value

    def __hash__(self) -> int:
        return hash(self.value)

    def __eq__(self, other: object) -> bool:
        if isinstance(other, PhoneNumber):
            return self.value == other.value
        if isinstance(other, str):
            return self.value == other
        return False

    @property
    def national_format(self) -> str:
        """Get phone number in national format."""
        try:
            parsed = phonenumbers.parse(self.value)
            return phonenumbers.format_number(
                parsed, phonenumbers.PhoneNumberFormat.NATIONAL
            )
        except NumberParseException:
            return self.value

    @property
    def international_format(self) -> str:
        """Get phone number in international format."""
        try:
            parsed = phonenumbers.parse(self.value)
            return phonenumbers.format_number(
                parsed, phonenumbers.PhoneNumberFormat.INTERNATIONAL
            )
        except NumberParseException:
            return self.value

    @property
    def is_valid(self) -> bool:
        """Check if the phone number is valid."""
        try:
            parsed = phonenumbers.parse(self.value)
            return phonenumbers.is_valid_number(parsed)
        except NumberParseException:
            return False
