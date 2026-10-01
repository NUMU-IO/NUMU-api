"""Password policy: the core length rule plus a breached-password check.

Follows NIST SP 800-63B: no composition rules (they push people to
``Password1``), a minimum length, and a refusal of passwords already known
from breaches. The breach check uses the Pwned Passwords k-anonymity range
API: only the first five hex characters of the SHA-1 leave this server.
"""

import hashlib

import httpx

from src.core.exceptions import ValidationError
from src.core.logging import get_logger
from src.core.validators.password import validate_password

logger = get_logger(__name__)

_RANGE_URL = "https://api.pwnedpasswords.com/range/{}"


async def _is_breached(password: str) -> bool:
    digest = hashlib.sha1(password.encode(), usedforsecurity=False).hexdigest().upper()
    prefix, suffix = digest[:5], digest[5:]
    try:
        async with httpx.AsyncClient(timeout=3) as client:
            res = await client.get(
                _RANGE_URL.format(prefix), headers={"Add-Padding": "true"}
            )
            res.raise_for_status()
    except httpx.HTTPError:
        # Fail open: an outage at the breach service must not block signups.
        logger.warning("pwned_passwords_unavailable", exc_info=True)
        return False
    for line in res.text.splitlines():
        candidate, _, count = line.partition(":")
        if candidate == suffix:
            # Padding rows carry a count of 0.
            return int(count or 0) > 0
    return False


async def enforce_password_policy(password: str) -> None:
    """Raise ValidationError(field="password") if the password is too short
    or has appeared in a known data breach."""
    validate_password(password)
    if await _is_breached(password):
        raise ValidationError(
            "Password has appeared in a known data breach. Choose a different one.",
            field="password",
        )
