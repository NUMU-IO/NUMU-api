"""Phone number value object."""

from typing import Any, Self

import phonenumbers
from phonenumbers import NumberParseException
from pydantic import BaseModel, ConfigDict, model_validator


class InvalidPhoneError(ValueError):
    """Raised when a phone number can't be parsed or fails validation.

    Distinct from the lenient ``PhoneNumber(value=...)`` constructor — use
    ``PhoneNumber.parse`` when you want strict E.164 normalisation and want
    bad input to be rejected (e.g. at API boundaries).
    """


class PhoneNumber(BaseModel):
    """Phone number value object with validation."""

    model_config = ConfigDict(frozen=True)

    value: str
    country_code: str = "EG"

    @classmethod
    def parse(cls, raw: str, default_region: str = "EG") -> "PhoneNumber":
        """Strictly parse ``raw`` into a canonical E.164 ``PhoneNumber``.

        ``default_region`` is the ISO 3166-1 alpha-2 hint used when ``raw``
        lacks a country code (e.g. ``01001234567`` + ``EG`` → ``+201001234567``).

        Raises ``InvalidPhoneError`` if the input can't be parsed or fails
        ``is_valid_number``. Unlike the lenient default constructor, this
        method never silently returns a non-E.164 value.
        """
        if raw is None:
            raise InvalidPhoneError("Phone number is required.")
        cleaned = raw.strip()
        if not cleaned:
            raise InvalidPhoneError("Phone number is required.")
        try:
            parsed = phonenumbers.parse(cleaned, (default_region or "EG").upper())
        except NumberParseException as exc:
            raise InvalidPhoneError(
                f"Could not parse phone number '{raw}': {exc}"
            ) from exc
        if not phonenumbers.is_valid_number(parsed):
            raise InvalidPhoneError(
                f"Phone number '{raw}' is not a valid number for region "
                f"'{default_region}'."
            )
        e164 = phonenumbers.format_number(parsed, phonenumbers.PhoneNumberFormat.E164)
        region = phonenumbers.region_code_for_number(parsed) or default_region
        return cls(value=e164, country_code=region.upper())

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

    @property
    def e164(self) -> str:
        """Canonical E.164 string (e.g. ``+201001234567``).

        Falls back to ``self.value`` if the stored value can't be re-parsed
        (only possible on legacy rows that bypassed ``.parse``).
        """
        try:
            parsed = phonenumbers.parse(self.value, self.country_code)
            if phonenumbers.is_valid_number(parsed):
                return phonenumbers.format_number(
                    parsed, phonenumbers.PhoneNumberFormat.E164
                )
        except NumberParseException:
            pass
        return self.value

    @property
    def formatted_international(self) -> str:
        """Pretty international format (e.g. ``+20 100 123 4567``).

        Alias of :pyattr:`international_format` so the public API matches the
        wording in the Part 1 plan. Read-only consumers use this; mutation
        paths should round-trip via ``.e164``.
        """
        return self.international_format
