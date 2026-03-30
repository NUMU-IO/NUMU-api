"""HMAC-SHA256 phone number hashing for network reputation lookups.

Normalizes phone numbers to E.164 format (+201012345678) BEFORE hashing.
Uses PLATFORM_SECRET_SALT from environment — never hardcoded or logged.

Security invariants:
- Raw phone numbers are NEVER stored or logged.
- Hashes are NEVER returned to the client or logged.
- The salt is read from settings at call time (supports rotation).
"""

from __future__ import annotations

import hashlib
import hmac
import re

# Egyptian mobile prefixes: 010, 011, 012, 015
_EGYPT_LOCAL_RE = re.compile(r"^0(1[0125]\d{8})$")
_EGYPT_INTL_RE = re.compile(r"^\+?20(1[0125]\d{8})$")


def normalize_phone_e164(phone: str) -> str | None:
    """Normalize an Egyptian phone number to E.164 format.

    Returns ``+20XXXXXXXXXX`` (13 chars) or ``None`` if the input
    cannot be recognized as a valid Egyptian mobile number.

    Examples::

        normalize_phone_e164("01012345678")    → "+201012345678"
        normalize_phone_e164("+201012345678")  → "+201012345678"
        normalize_phone_e164("201012345678")   → "+201012345678"
        normalize_phone_e164("123")            → None
    """
    if not phone:
        return None

    cleaned = (
        phone.strip()
        .replace(" ", "")
        .replace("-", "")
        .replace("(", "")
        .replace(")", "")
    )

    # +20XXXXXXXXXX or 20XXXXXXXXXX
    m = _EGYPT_INTL_RE.match(cleaned)
    if m:
        return f"+20{m.group(1)}"

    # 0XXXXXXXXXX (local format)
    m = _EGYPT_LOCAL_RE.match(cleaned)
    if m:
        return f"+20{m.group(1)}"

    return None


def hash_phone(phone_e164: str, salt: str) -> str:
    """Compute HMAC-SHA256 of an E.164 phone number using the platform salt.

    Parameters
    ----------
    phone_e164:
        Phone number already in E.164 format (e.g. ``"+201012345678"``).
        Caller MUST normalize before calling this function.
    salt:
        The ``PLATFORM_SECRET_SALT`` value (hex-encoded 256-bit key).

    Returns
    -------
    str
        64-character lowercase hex digest.
    """
    return hmac.new(
        salt.encode("utf-8"),
        phone_e164.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()


def normalize_and_hash(phone: str, salt: str) -> str | None:
    """Normalize phone to E.164 then HMAC-SHA256 hash it.

    Returns the 64-char hex digest, or ``None`` if the phone number
    cannot be normalized (invalid format).
    """
    e164 = normalize_phone_e164(phone)
    if e164 is None:
        return None
    return hash_phone(e164, salt)


def normalize_and_hash_dual(
    phone: str, salt: str, old_salt: str | None = None
) -> tuple[str | None, str | None]:
    """Hash with current salt, and optionally with old salt for rotation.

    During salt rotation, callers should write events under BOTH hashes
    to maintain continuity.  Once the rotation window closes, old_salt
    lookups can be dropped.

    Returns ``(current_hash, old_hash)`` — old_hash is ``None`` if
    ``old_salt`` is not provided.
    """
    e164 = normalize_phone_e164(phone)
    if e164 is None:
        return None, None
    current = hash_phone(e164, salt)
    old = hash_phone(e164, old_salt) if old_salt else None
    return current, old
