"""PII hashing helpers for Meta Conversions API.

Meta requires user_data PII fields (``em``, ``ph``, ``fn``, ``ln``,
``ct``, ``st``, ``zp``, ``country``, ``db``, ``external_id``) to be
SHA-256 hashed lowercase-trimmed before transmission. ``fbp``, ``fbc``,
``client_ip_address`` and ``client_user_agent`` are passed verbatim
per Meta's spec.

This module is the single source of truth — any caller hashing PII for
Meta MUST go through ``hash_user_data()``. Direct ``hashlib.sha256``
calls scattered across the codebase are a footgun (case-sensitivity,
trim, phone-format inconsistencies all silently destroy match quality).

Implementation matches the plan §5.6 spec verbatim.
"""

import hashlib


def _h(s: str | None) -> str | None:
    """SHA-256 the lowercase-trimmed UTF-8 bytes of ``s``.

    Returns None when ``s`` is None or empty so callers can skip the
    field entirely (Meta drops nulls server-side, but sending None
    keys is wasteful and slightly degrades match quality scoring).
    """
    if not s:
        return None
    return hashlib.sha256(s.strip().lower().encode()).hexdigest()


def hash_user_data(raw: dict) -> dict:
    """Convert a raw user-data dict into Meta's hashed CAPI shape.

    Input keys are NUMU's internal vocabulary (``email``, ``phone``,
    ``first_name``, ``city`` …); output keys match Meta's CAPI spec
    (``em``, ``ph``, ``fn``, ``ct`` …) with values wrapped in single-
    element lists per Meta's hashed-field contract.

    Fields not in ``raw`` are emitted as ``None`` (Meta tolerates and
    drops them server-side); this keeps the payload shape stable for
    ``meta_event_log.request_payload`` redaction logic.
    """
    return {
        "em": [_h(raw["email"])] if raw.get("email") else None,
        "ph": [_h(_normalize_eg_phone(raw["phone"]))] if raw.get("phone") else None,
        "fn": [_h(raw["first_name"])] if raw.get("first_name") else None,
        "ln": [_h(raw["last_name"])] if raw.get("last_name") else None,
        "ct": [_h(raw["city"])] if raw.get("city") else None,
        "country": [_h(raw["country_code"])] if raw.get("country_code") else None,
        "zp": [_h(raw["zip"])] if raw.get("zip") else None,
        # NOT hashed — Meta wants these raw:
        "fbp": raw.get("fbp"),
        "fbc": raw.get("fbc"),
        "client_ip_address": raw.get("ip"),
        "client_user_agent": raw.get("user_agent"),
        "external_id": [_h(raw["customer_id"])] if raw.get("customer_id") else None,
    }


def _normalize_eg_phone(phone: str) -> str:
    """Normalize an Egyptian mobile number to E.164-without-plus form.

    Accepts any of:
        +201001234567   (E.164 with +)
        201001234567    (E.164 without +)
        01001234567     (national format)
        ٠١٠٠١٢٣٤٥٦٧     (Arabic-Indic digits — handled via isdigit())

    Returns "20" + 10-digit subscriber number (e.g. "201001234567").
    Always returning the same canonical shape is what makes the SHA-256
    hash match across browser-side Pixel and server-side CAPI.
    """
    digits = "".join(c for c in phone if c.isdigit())
    if digits.startswith("20"):
        digits = digits[2:]
    if digits.startswith("0"):
        digits = digits[1:]
    return "20" + digits  # E.164 without the +
