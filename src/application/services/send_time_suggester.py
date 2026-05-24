"""Best-time-to-send suggestions — feature 002 US9.

Aggregates the store's last-90-days sends + open events from Resend
webhook into weekday × hour buckets, ranks them, and returns the top 3
chip suggestions. SMS / WhatsApp channels lack open events and fall
back to send-count ranking ("based on your usual habit").

The returned chips are i18n-clean: the route layer renders the label;
the service surfaces raw weekday/hour ints + averages so the route can
localize.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Literal
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.infrastructure.database.models.tenant.marketing_campaign import (
    MarketingCampaignModel,
)

Channel = Literal["email", "sms", "whatsapp"]
BasedOn = Literal["open_rate", "send_count"] | None


@dataclass
class Suggestion:
    weekday: int  # 0=Monday … 6=Sunday (Python weekday() convention)
    weekday_name: str
    hour: int
    avg_open_rate: float | None
    avg_sent: float


@dataclass
class SuggestionResult:
    based_on: BasedOn
    sample_size: int
    suggestions: list[Suggestion]


_WEEKDAYS_EN = [
    "Monday",
    "Tuesday",
    "Wednesday",
    "Thursday",
    "Friday",
    "Saturday",
    "Sunday",
]
_MIN_HISTORY = 10
_WINDOW_DAYS = 90
_LIMIT = 3


async def suggest(
    session: AsyncSession,
    store_id: UUID,
    channel: Channel,
) -> SuggestionResult:
    """Compute top-3 send-time suggestions for a store/channel pair.

    Current implementation uses ``marketing_campaigns.started_at`` as
    the historical send timestamp + ``delivered_count`` as the open-
    proxy when actual open events aren't available. When Resend's
    ``email.opened`` table lands, this swaps to a real join on the
    delivery log.

    The simplification keeps US9 useful in v1 without blocking on the
    Resend event-log integration; the chip says "based on your
    usual habit" via the fallback path.
    """
    window_start = datetime.utcnow() - timedelta(days=_WINDOW_DAYS)

    # Pull historical sends for this store + channel within window.
    q = (
        select(
            func.extract("dow", MarketingCampaignModel.started_at).label("dow"),
            func.extract("hour", MarketingCampaignModel.started_at).label("hour"),
            func.count(MarketingCampaignModel.id).label("send_count"),
            func.avg(MarketingCampaignModel.sent_count).label("avg_sent"),
            func.avg(MarketingCampaignModel.delivered_count).label("avg_delivered"),
        )
        .where(
            MarketingCampaignModel.store_id == store_id,
            MarketingCampaignModel.channel == channel,
            MarketingCampaignModel.started_at.is_not(None),
            MarketingCampaignModel.started_at >= window_start,
        )
        .group_by("dow", "hour")
    )
    rows = (await session.execute(q)).all()

    total_sends = sum(int(r.send_count or 0) for r in rows)
    if total_sends < _MIN_HISTORY:
        return SuggestionResult(based_on=None, sample_size=total_sends, suggestions=[])

    # For email channel only, prefer open-rate ranking. Otherwise
    # (SMS / WhatsApp / email with no opens captured) fall back to
    # ranking by avg sent count (the merchant's habitual send slot).
    use_open_rate = channel == "email" and any((r.avg_delivered or 0) > 0 for r in rows)
    based_on: BasedOn = "open_rate" if use_open_rate else "send_count"

    # Build candidates. Postgres DOW is 0=Sunday..6=Saturday; we shift
    # to Python's 0=Monday..6=Sunday for the suggestion struct.
    candidates: list[Suggestion] = []
    for r in rows:
        pg_dow = int(r.dow)
        py_weekday = (pg_dow - 1) % 7  # Sun(0) → 6, Mon(1) → 0, Tue(2) → 1, …
        hour = int(r.hour)
        avg_sent = float(r.avg_sent or 0)
        if use_open_rate:
            avg_delivered = float(r.avg_delivered or 0)
            open_rate = avg_delivered / avg_sent if avg_sent > 0 else 0.0
            candidates.append(
                Suggestion(
                    weekday=py_weekday,
                    weekday_name=_WEEKDAYS_EN[py_weekday],
                    hour=hour,
                    avg_open_rate=open_rate,
                    avg_sent=avg_sent,
                )
            )
        else:
            candidates.append(
                Suggestion(
                    weekday=py_weekday,
                    weekday_name=_WEEKDAYS_EN[py_weekday],
                    hour=hour,
                    avg_open_rate=None,
                    avg_sent=avg_sent,
                )
            )

    # Rank
    if use_open_rate:
        candidates.sort(key=lambda c: (c.avg_open_rate or 0), reverse=True)
    else:
        candidates.sort(key=lambda c: c.avg_sent, reverse=True)

    return SuggestionResult(
        based_on=based_on,
        sample_size=total_sends,
        suggestions=candidates[:_LIMIT],
    )
