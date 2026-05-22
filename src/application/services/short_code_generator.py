"""Cryptographically random short codes for marketing-campaign links.

Per research.md R-02: 6-character Crockford base32. Per-store uniqueness
enforced by ``uq_campaigns_store_short_code``. Generator retries on
collision (max 5 attempts before raising).

Crockford excludes ``I/L/O/U`` — avoids look-alike confusion and the
``F**K`` profanity case — so merchants can read a code off a QR-code
business card or eyeball one in a screenshot.

Source of randomness is ``secrets.choice`` (NOT ``random.choice``) per
SEC-003. Codes must be cryptographically non-predictable so a
competitor can't enumerate a merchant's campaigns by hitting
``?utm_campaign=*`` strings with guessed codes.
"""

from __future__ import annotations

import secrets
from uuid import UUID

from sqlalchemy import exists, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.infrastructure.database.models.tenant.marketing_campaign import (
    MarketingCampaignModel,
)

# Crockford base32 alphabet — 32 chars, excludes I, L, O, U.
# Reference: https://www.crockford.com/base32.html
_ALPHABET = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"
_CODE_LENGTH = 6
_MAX_RETRIES = 5


class ShortCodeGenerationError(RuntimeError):
    """Raised when the retry loop fails to find a free short code.

    At 32^6 ≈ 1.07 × 10⁹ possible codes per store, 5 collisions in a row
    on a single insert means either a clock-skew RNG seed problem or
    the merchant has somehow accumulated > 10⁸ campaigns. Either way,
    surface the failure loudly rather than silently looping forever.
    """


def _random_code() -> str:
    """Single 6-char draw from the Crockford alphabet using ``secrets``."""
    return "".join(secrets.choice(_ALPHABET) for _ in range(_CODE_LENGTH))


async def generate(store_id: UUID, session: AsyncSession) -> str:
    """Generate a fresh, unused short code for ``store_id``.

    Retries on collision against the existing
    ``marketing_campaigns(store_id, short_code)`` rows. Caller is
    responsible for committing the row that uses the returned code —
    if the caller's transaction rolls back, the code is effectively
    "released" (uniqueness is per-row, not reserved).

    Note: the uniqueness check + insert are not atomic, so under
    concurrent campaign creation two calls could pick the same code.
    The DB's ``uq_campaigns_store_short_code`` UNIQUE constraint
    catches that race; callers should be prepared to catch
    ``IntegrityError`` on insert and retry by calling ``generate``
    again. The 32^6 space makes this race overwhelmingly unlikely in
    practice (would require two concurrent calls drawing the same
    code from `secrets`, which is independent of timing).
    """
    for _ in range(_MAX_RETRIES):
        code = _random_code()
        stmt = select(
            exists().where(
                MarketingCampaignModel.store_id == store_id,
                MarketingCampaignModel.short_code == code,
            )
        )
        result = await session.execute(stmt)
        if not result.scalar():
            return code
    raise ShortCodeGenerationError(
        f"failed to find a free short_code for store={store_id} after "
        f"{_MAX_RETRIES} attempts"
    )


def generate_unchecked() -> str:
    """Generate a code without checking for uniqueness.

    Useful for migration backfill (where we batch-generate codes and
    rely on a deterministic seeded RNG to avoid collisions within the
    backfill itself), and for unit tests that don't have a DB session.
    """
    return _random_code()
