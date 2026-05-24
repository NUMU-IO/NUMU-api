"""Best-time-to-send chip suggestions — feature 002 US9.

Endpoint: GET /api/v1/stores/{store_id}/marketing/send-time-suggestions
Cached in-memory per (store_id, channel) for 1 hour to satisfy
SC-010's 200ms p95 budget without hammering the DB on every Schedule
dialog open.
"""

from __future__ import annotations

from typing import Literal
from uuid import UUID

from cachetools import TTLCache
from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel

from src.api.dependencies import verify_store_ownership
from src.api.responses import SuccessResponse
from src.application.services.send_time_suggester import (
    Channel,
    SuggestionResult,
    suggest,
)
from src.infrastructure.database.connection import AsyncSessionLocal

router = APIRouter(
    prefix="/{store_id}/marketing",
    tags=["Marketing Send Times"],
    dependencies=[Depends(verify_store_ownership)],
)


# SEC-005: cache key is (store_id, channel) — both components MUST be
# in the key. A bug that dropped store_id would serve store A's
# suggestions to store B (cross-tenant data leak via cache).
_CACHE: TTLCache[tuple[str, str], SuggestionResult] = TTLCache(maxsize=2048, ttl=3600)


# ── Schemas ──────────────────────────────────────────────────────


class SuggestionResponse(BaseModel):
    weekday: int
    weekday_name: str
    hour: int
    avg_open_rate: float | None
    avg_sent: float
    label: str


class SendTimeResponse(BaseModel):
    store_id: str
    channel: Channel
    tz: str
    based_on: str | None
    sample_size: int
    suggestions: list[SuggestionResponse]


def _format_label(s, isAr: bool, based_on: str | None) -> str:
    """Server-side render the chip label so the UI just displays."""
    hour_label = _format_hour(s.hour, isAr)
    weekday_short = s.weekday_name[:3]
    if based_on == "open_rate" and s.avg_open_rate is not None:
        pct = int(round(s.avg_open_rate * 100))
        if isAr:
            return f"{weekday_short} {hour_label} ({pct}٪ فتح)"
        return f"{weekday_short} {hour_label} (avg {pct}% open)"
    # Fallback / send_count
    if isAr:
        return f"{weekday_short} {hour_label} (وقتك المعتاد)"
    return f"{weekday_short} {hour_label} (your usual time)"


def _format_hour(hour24: int, isAr: bool) -> str:
    if isAr:
        return f"{hour24:02d}:00"
    suffix = "AM" if hour24 < 12 else "PM"
    h12 = hour24 % 12 or 12
    return f"{h12} {suffix}"


# ── Route ────────────────────────────────────────────────────────


@router.get(
    "/send-time-suggestions",
    response_model=SuccessResponse[SendTimeResponse],
    summary="Best-time-to-send chip suggestions for the Schedule dialog",
    operation_id="get_send_time_suggestions",
)
async def get_send_time_suggestions(
    store_id: UUID,
    channel: Channel = Query("email"),
    tz: str = Query("Africa/Cairo"),
    locale: Literal["en", "ar"] = Query("en"),
):
    """Returns 0-3 chip suggestions. Empty when the store has < 10
    prior sends in this channel (FR-042).
    """
    cache_key = (str(store_id), channel)
    cached = _CACHE.get(cache_key)
    if cached is None:
        async with AsyncSessionLocal() as session:
            cached = await suggest(session, store_id, channel)
        _CACHE[cache_key] = cached

    is_ar = locale == "ar"
    chips = [
        SuggestionResponse(
            weekday=s.weekday,
            weekday_name=s.weekday_name,
            hour=s.hour,
            avg_open_rate=s.avg_open_rate,
            avg_sent=s.avg_sent,
            label=_format_label(s, is_ar, cached.based_on),
        )
        for s in cached.suggestions
    ]

    if not chips:
        msg = "Not enough send history for suggestions yet (need 10+ prior sends)"
    elif cached.based_on == "send_count":
        msg = "Best-time suggestions for store (no open data — using send-count habit)"
    else:
        msg = "Best-time suggestions for store"

    return SuccessResponse(
        data=SendTimeResponse(
            store_id=str(store_id),
            channel=channel,
            tz=tz,
            based_on=cached.based_on,
            sample_size=cached.sample_size,
            suggestions=chips,
        ),
        message=msg,
    )
