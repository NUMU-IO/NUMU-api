"""Shared date-range query parameters for analytics endpoints.

Every analytics & dashboard endpoint accepts the same trio:

    ?start_date=<ISO>&end_date=<ISO>&granularity=<g>

The legacy `?days=N` parameter is still honored — when neither
`start_date` nor `end_date` is present, we fall back to
``[now - days, now]`` so existing callers (older mobile app builds,
saved bookmarks) keep working for one release cycle.

Span clamping bounds the worst-case query cost:
- ``hour`` bucketing caps at 7 days  (=> 168 buckets max)
- ``day`` bucketing caps at 365 days (=> 365 buckets max)
- ``week``/``month``/``quarter``/``year`` cap at 5 years

Times are normalized to UTC; naive datetimes are assumed UTC. Calendar-day
projections (``start_date``/``end_date``) are taken on the STORE's wall
clock, not UTC: the optional ``tz`` query param carries the store's IANA
timezone (the hub sends it from store settings) and defaults to
Africa/Cairo — the platform's home market. Bucketing by UTC used to shift
every after-midnight Cairo order onto the previous day's charts.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from typing import Annotated, Literal

from fastapi import Depends, HTTPException, Query, status
from pydantic import BaseModel, ConfigDict

from src.core.utils.store_timezone import (
    DEFAULT_STORE_TIMEZONE,
    resolve_store_timezone_name,
    safe_zone,
)

Granularity = Literal["hour", "day", "week", "month", "quarter", "year"]

# Max span per granularity (in days). Tuned to keep response payloads
# and downstream group-by queries bounded.
_MAX_SPAN_DAYS: dict[Granularity, int] = {
    "hour": 7,
    "day": 365,
    "week": 365 * 2,
    "month": 365 * 5,
    "quarter": 365 * 5,
    "year": 365 * 10,
}


class DateRangeWindow(BaseModel):
    """Normalized analytics window passed into route handlers.

    `start` / `end` are UTC datetimes (inclusive). `start_date` /
    `end_date` are the calendar-date projections **on the store's wall
    clock** (`tz`) for endpoints that query a daily rollups table keyed
    on `date` — the rollup task keys its rows on the same local days.

    `tz` is the validated IANA timezone name; repos that bucket by
    day/hour must pass it into their `AT TIME ZONE` conversions.

    `days` is provided as a convenience for log lines and cache keys;
    endpoints should not depend on it for correctness.
    """

    model_config = ConfigDict(frozen=True)

    start: datetime
    end: datetime
    start_date: date
    end_date: date
    days: int
    granularity: Granularity
    tz: str = DEFAULT_STORE_TIMEZONE


def _parse_iso(value: str, field: str) -> datetime:
    try:
        # Accept "...Z", "...+00:00", or bare local — Python 3.11+
        # `fromisoformat` handles all of these.
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Invalid ISO datetime for {field}: {value}",
        ) from e
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def resolve_date_range_window(
    *,
    days: int | None = None,
    start_date: str | None = None,
    end_date: str | None = None,
    granularity: Granularity = "day",
    tz: str | None = None,
) -> DateRangeWindow:
    """Pure parser used by both the FastAPI dependency and unit tests.

    Resolution order:
      1. `start_date` + `end_date` (preferred new path)
      2. `days` (legacy fallback)
      3. Default: last 30 days

    Raises 422 when only one half of the start/end pair is supplied,
    when `start > end`, or when the resulting span exceeds the
    granularity cap.
    """
    now = datetime.now(UTC)

    has_start = start_date is not None
    has_end = end_date is not None
    if has_start ^ has_end:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="start_date and end_date must be provided together",
        )

    if has_start and has_end:
        start = _parse_iso(start_date, "start_date")
        end = _parse_iso(end_date, "end_date")
    elif days is not None:
        end = now
        start = end - timedelta(days=days)
    else:
        end = now
        start = end - timedelta(days=30)

    if start > end:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="start_date must be <= end_date",
        )

    span_days = max(1, (end.date() - start.date()).days + 1)
    cap = _MAX_SPAN_DAYS[granularity]
    if span_days > cap:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=(
                f"Span of {span_days} day(s) exceeds the cap of {cap} "
                f"for granularity={granularity}"
            ),
        )

    # Calendar-date projections on the store's wall clock. A malformed tz
    # degrades to the platform default rather than 422ing — the window
    # instants are still exactly what the caller asked for.
    tz_name = resolve_store_timezone_name({"timezone": tz} if tz else None)
    zone = safe_zone(tz_name)
    local_start_date = start.astimezone(zone).date()
    local_end_date = end.astimezone(zone).date()

    return DateRangeWindow(
        start=start,
        end=end,
        start_date=local_start_date,
        end_date=local_end_date,
        days=max(1, (local_end_date - local_start_date).days + 1),
        granularity=granularity,
        tz=tz_name,
    )


def get_date_range_window(
    days: int | None = Query(
        None,
        ge=1,
        le=3650,
        description="Legacy: number of days back from now. Use "
        "start_date+end_date instead. Ignored when those are present.",
    ),
    start_date: str | None = Query(
        None,
        description="Window start (ISO datetime). Required together with end_date.",
    ),
    end_date: str | None = Query(
        None,
        description="Window end (ISO datetime). Required together with start_date.",
    ),
    granularity: Granularity = Query(
        "day",
        description="Bucket size for timeseries endpoints. Non-timeseries "
        "endpoints ignore this parameter.",
    ),
    tz: str | None = Query(
        None,
        max_length=64,
        description="IANA timezone for calendar-day bucketing (e.g. "
        "Africa/Cairo). Send the store's timezone. Invalid or missing "
        "values fall back to Africa/Cairo.",
    ),
) -> DateRangeWindow:
    """FastAPI dependency wrapping ``resolve_date_range_window``."""
    return resolve_date_range_window(
        days=days,
        start_date=start_date,
        end_date=end_date,
        granularity=granularity,
        tz=tz,
    )


DateRangeDep = Annotated[DateRangeWindow, Depends(get_date_range_window)]
