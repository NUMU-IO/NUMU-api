"""Resolve a raw ``utm_campaign`` string to a MarketingCampaign row.

The trackable-link builder embeds the campaign's stable ``short_code``
as the suffix of ``utm_campaign``::

    eid-sale-2026-AB7K9X
    └──────┬────────┘ └─┬┘
           name slug    6-char Crockford base32 short_code

The resolver splits off the suffix and looks up the campaign by
``(store_id, short_code)``. Tenant-scoped: a short_code from store A
can never resolve to a campaign on store B (SEC-006 / FR-011).

When no match is found (typo, hand-edited link, foreign UTM, stale
short_code from a deleted campaign), returns ``None``. The order /
funnel event still records the raw UTM strings — only ``campaign_id``
goes NULL.
"""

from __future__ import annotations

import re
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.infrastructure.database.models.tenant.marketing_campaign import (
    MarketingCampaignModel,
)

# Trailing block: hyphen + 6 chars from the Crockford alphabet.
# Anchored to end of string so the body of the slug can contain
# hyphens freely.
_SHORT_CODE_SUFFIX = re.compile(r"-([0-9A-HJKMNP-TV-Z]{6})$", re.IGNORECASE)


def _extract_short_code(utm_campaign: str) -> str | None:
    """Pull the trailing 6-char Crockford short_code from a utm_campaign.

    Returns the uppercase short_code on match, ``None`` if the string
    doesn't end with the expected ``-XXXXXX`` block. Used by the
    resolver and reusable from any place that needs to recover the
    code from a raw UTM string (e.g. funnel event ingest).
    """
    match = _SHORT_CODE_SUFFIX.search(utm_campaign)
    if not match:
        return None
    return match.group(1).upper()


async def resolve_campaign_id(
    *,
    session: AsyncSession,
    store_id: UUID,
    utm_campaign: str | None,
) -> UUID | None:
    """Look up a MarketingCampaign by short_code, scoped to ``store_id``.

    Returns the campaign UUID on a hit, ``None`` if:
      * ``utm_campaign`` is None / empty
      * The string doesn't carry a Crockford suffix (foreign UTM)
      * No campaign on this store matches that short_code

    Critical (SEC-001 + SEC-006): the WHERE clause filters BOTH
    ``store_id`` and ``short_code``. Never widen this to short_code
    alone — that would let store A's traffic stamp store B's
    campaign_id onto an order.
    """
    if not utm_campaign:
        return None
    short_code = _extract_short_code(utm_campaign)
    if not short_code:
        return None
    stmt = (
        select(MarketingCampaignModel.id)
        .where(MarketingCampaignModel.store_id == store_id)
        .where(MarketingCampaignModel.short_code == short_code)
        .limit(1)
    )
    result = await session.execute(stmt)
    row = result.scalar_one_or_none()
    return row
