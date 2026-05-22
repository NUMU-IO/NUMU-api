"""Campaign-attached coupon issuance service.

Generates a human-readable discount code that ties a coupon row to a
marketing campaign:

    <CAMPAIGN-SLUG>-<6-CHAR-CROCKFORD>

Example: ``EID-SALE-2026-AB7K9X``. The slug half is read by humans
(merchants can eyeball which campaign a code belongs to in support
threads); the Crockford half guarantees uniqueness within the
store-scoped coupons.code namespace.

Why Crockford, not just letters: same reason as the campaign
short_code (I/L/O/U excluded so a printed code on a flyer doesn't get
misread between O/0 or I/1). Why 6 chars: 32^6 = ~1B per store —
overkill for a single store's coupon namespace but matches the
existing short_code length for consistency.
"""

from __future__ import annotations

import re
import secrets
from datetime import datetime
from decimal import Decimal
from uuid import UUID

from sqlalchemy import exists, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.entities.coupon import Coupon, CouponType
from src.core.entities.marketing_campaign import MarketingCampaign
from src.infrastructure.database.models.tenant.coupon import CouponModel

_CROCKFORD_ALPHABET = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"
_SUFFIX_LENGTH = 6
_MAX_RETRIES = 5

# Reasonable cap on the slug portion. Coupons.code is VARCHAR(50);
# with a 6-char suffix + dash that leaves ~43 chars for the prefix.
# We cap at 32 to leave headroom in case the customer's email template
# concatenates more — and because 32-char campaign-name prefixes are
# already pushing readability.
_MAX_SLUG_LEN = 32


class CampaignCouponGenerationError(RuntimeError):
    """Raised when the retry loop fails to find a free code.

    Per-store 32^6 ≈ 1B namespace; five consecutive collisions is a
    bug or a hot RNG-seed issue, not a normal race.
    """


def _slugify_for_coupon(name: str) -> str:
    """Uppercase ASCII slug, truncated, dash-separated.

    Coupon codes are uppercase by convention (see
    ``Coupon.normalize_code``). Non-ASCII names (common in NUMU's
    Arabic-speaking market) often slugify down to nothing — fall back
    to a generic ``CAMPAIGN`` prefix in that case so the suffix still
    keeps the code unique.
    """
    # Keep ASCII letters and digits, replace everything else with -
    cleaned = re.sub(r"[^A-Za-z0-9]+", "-", name).strip("-").upper()
    if not cleaned:
        return "CAMPAIGN"
    return cleaned[:_MAX_SLUG_LEN]


def _random_suffix() -> str:
    """6-char Crockford suffix using cryptographic randomness."""
    return "".join(secrets.choice(_CROCKFORD_ALPHABET) for _ in range(_SUFFIX_LENGTH))


async def generate_unique_code(
    *,
    session: AsyncSession,
    store_id: UUID,
    campaign_name: str,
) -> str:
    """Generate a per-store-unique campaign-attached code.

    Retries on collision against the existing ``coupons(store_id, code)``
    UNIQUE constraint. The DB constraint backstops the in-memory check
    in case of a race between the lookup and the eventual insert.
    """
    slug = _slugify_for_coupon(campaign_name)
    for _ in range(_MAX_RETRIES):
        candidate = f"{slug}-{_random_suffix()}"
        result = await session.execute(
            select(
                exists().where(
                    CouponModel.store_id == store_id,
                    CouponModel.code == candidate,
                )
            )
        )
        if not result.scalar():
            return candidate
    raise CampaignCouponGenerationError(
        f"failed to find a free coupon code for store={store_id} "
        f"campaign={campaign_name!r} after {_MAX_RETRIES} attempts"
    )


def build_campaign_coupon(
    *,
    store_id: UUID,
    tenant_id: UUID,
    campaign: MarketingCampaign,
    code: str,
    coupon_type: CouponType,
    value: Decimal,
    min_order_amount: Decimal | None = None,
    max_discount_amount: Decimal | None = None,
    usage_limit: int | None = None,
    valid_from: datetime | None = None,
    valid_until: datetime | None = None,
) -> Coupon:
    """Build a Coupon entity wired to a campaign.

    Validation that's specific to the campaign-issuance path:
    * PERCENTAGE coupons cap at 100 (Coupon's base validator allows
      higher; for a campaign-issued discount we treat >100 as merchant
      error to avoid free-money configurations).
    * FIXED coupons require a positive value (no zero-value codes).

    The Coupon entity already enforces uppercased + stripped code via
    ``normalize_code``; we pass the generator's output verbatim.
    """
    if coupon_type == CouponType.PERCENTAGE and value > 100:
        raise ValueError(f"percentage coupon value cannot exceed 100; got {value}")
    if coupon_type == CouponType.FIXED and value <= 0:
        raise ValueError("fixed-amount coupon value must be positive")

    return Coupon(
        store_id=store_id,
        tenant_id=tenant_id,
        code=code,
        coupon_type=coupon_type,
        value=value,
        min_order_amount=min_order_amount,
        max_discount_amount=max_discount_amount,
        usage_limit=usage_limit,
        valid_from=valid_from,
        valid_until=valid_until,
        is_active=True,
        campaign_id=campaign.id,
    )
