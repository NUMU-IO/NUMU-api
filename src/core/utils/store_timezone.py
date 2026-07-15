"""Store-timezone resolution — the single source of truth for "a day".

Analytics historically bucketed everything by UTC midnight while the
platform's merchants operate on Egyptian wall-clock time (UTC+2, no DST
since 2023 reintroduction is handled by tzdata anyway). Orders placed
00:00–02:59 Cairo landed on the *previous* day's charts and rollups, and
"peak hour" histograms were shifted by 2–3 hours. Every consumer of a
calendar-day or hour-of-day boundary must resolve it through this module
so the platform has exactly one definition of "today".

The store's timezone lives at ``store.settings["timezone"]`` (IANA name).
It is optional; the platform default is Africa/Cairo — the correct choice
for every current production store. Saudi stores set Asia/Riyadh.

This module is pure (stdlib only) so core/application/infrastructure/api
can all import it without layering violations.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

DEFAULT_STORE_TIMEZONE = "Africa/Cairo"


def resolve_store_timezone_name(settings: dict | None) -> str:
    """Return the store's validated IANA timezone name.

    Falls back to ``DEFAULT_STORE_TIMEZONE`` when the setting is absent,
    blank, or not a resolvable zone — a bad merchant-entered value must
    degrade to Cairo, never crash an analytics query.
    """
    raw = (settings or {}).get("timezone")
    name = raw.strip() if isinstance(raw, str) else ""
    if not name:
        return DEFAULT_STORE_TIMEZONE
    try:
        ZoneInfo(name)
    except Exception:
        return DEFAULT_STORE_TIMEZONE
    return name


def safe_zone(tz_name: str | None) -> ZoneInfo:
    """``ZoneInfo`` for ``tz_name``, falling back to the platform default."""
    if tz_name:
        try:
            return ZoneInfo(tz_name)
        except Exception:
            pass
    return ZoneInfo(DEFAULT_STORE_TIMEZONE)


def local_day_bounds(day: date, tz_name: str) -> tuple[datetime, datetime]:
    """UTC instants ``[start, end)`` covering calendar ``day`` in ``tz_name``.

    ``end`` is exclusive (start of the next local day) so callers can use
    the half-open ``created_at >= start AND created_at < end`` idiom
    without double-counting midnight rows.
    """
    tz = safe_zone(tz_name)
    start = datetime(day.year, day.month, day.day, tzinfo=tz)
    end = start + timedelta(days=1)
    return start.astimezone(UTC), end.astimezone(UTC)


def local_date(instant: datetime, tz_name: str) -> date:
    """The calendar date of ``instant`` on the ``tz_name`` wall clock."""
    if instant.tzinfo is None:
        instant = instant.replace(tzinfo=UTC)
    return instant.astimezone(safe_zone(tz_name)).date()
