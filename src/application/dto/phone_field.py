"""Pydantic field type for international phone numbers.

Accepts either form on the wire:

- A plain string already in E.164 form (``"+201001234567"``) **or** a local
  number that can be parsed against the request's default region
  (``"01001234567"`` + default region ``EG``).
- An object ``{"country_code": "EG", "local": "01001234567"}``. The
  ``country_code`` is an ISO 3166-1 alpha-2 region hint; the ``local``
  field is the user-typed digits.

Always serialises out as the canonical E.164 string (``"+201001234567"``)
so callers don't need to know what shape was sent in. Invalid input
raises :class:`~src.core.value_objects.phone.InvalidPhoneError`, which
Pydantic surfaces as a 422 validation error at the API boundary.

Usage::

    from src.application.dto.phone_field import PhoneField

    class RegisterRequest(BaseModel):
        phone: PhoneField = Field(None, description="Phone number")

``PhoneField`` is itself optional (``str | None``) — schemas should NOT
wrap it in another ``| None`` because Pydantic union dispatch would
then pin the inbound payload to the ``str`` arm before our validator
can map ``""`` or ``None`` to ``None``.

The frontends mount ``libphonenumber-js`` and submit canonical E.164, so
in practice every inbound payload is the string form. The dict form is
kept for callers (admin tooling, third-party imports) that prefer to
pass the pieces separately.
"""

from typing import Annotated, Any

from pydantic import BeforeValidator

from src.core.value_objects.phone import InvalidPhoneError, PhoneNumber

# Default region for parsing local-format numbers. Egypt is still the
# overwhelmingly common case; per-request overrides happen at the
# storefront layer where the store's country is known.
_DEFAULT_REGION = "EG"


def _coerce_to_e164(value: Any) -> Any:
    """Normalise inbound phone payloads to a canonical E.164 string."""
    if value is None:
        return None
    if isinstance(value, PhoneNumber):
        return value.e164
    if isinstance(value, str):
        cleaned = value.strip()
        if not cleaned:
            return None
        return PhoneNumber.parse(cleaned, default_region=_DEFAULT_REGION).e164
    if isinstance(value, dict):
        local = value.get("local") or value.get("number") or value.get("value")
        region = value.get("country_code") or value.get("region") or _DEFAULT_REGION
        if local is None:
            raise InvalidPhoneError(
                "Phone object must include a 'local' (or 'number') field."
            )
        if not isinstance(local, str) or not isinstance(region, str):
            raise InvalidPhoneError("Phone object fields must be strings.")
        return PhoneNumber.parse(local, default_region=region).e164
    raise InvalidPhoneError(f"Unsupported phone payload type: {type(value).__name__}.")


# Annotated Pydantic type. Use as ``phone: PhoneField = Field(None, ...)``.
# ``str | None`` lives inside the annotated tuple so the BeforeValidator
# can legitimately return ``None`` (e.g. when the wire value was ``""``)
# without Pydantic complaining that ``None`` isn't a valid ``str``.
PhoneField = Annotated[str | None, BeforeValidator(_coerce_to_e164)]
