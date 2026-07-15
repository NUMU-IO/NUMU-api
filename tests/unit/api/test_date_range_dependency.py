"""Tests for the shared date-range query dependency."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from fastapi import HTTPException

from src.api.dependencies.date_range import (
    DateRangeWindow,
    resolve_date_range_window,
)


def test_default_returns_last_30_days() -> None:
    win = resolve_date_range_window()
    assert isinstance(win, DateRangeWindow)
    assert win.granularity == "day"
    assert win.days == 31  # inclusive span: today + 30 prior days
    assert win.end > win.start
    # End sits within a minute of "now".
    assert (datetime.now(UTC) - win.end) < timedelta(minutes=1)


def test_legacy_days_parameter_still_works() -> None:
    win = resolve_date_range_window(days=7)
    assert win.days == 8
    span = (win.end - win.start).total_seconds()
    assert abs(span - 7 * 86400) < 5  # within a few seconds


def test_explicit_start_end_take_precedence() -> None:
    win = resolve_date_range_window(
        days=1000,
        start_date="2026-05-01T00:00:00Z",
        end_date="2026-05-15T23:59:59Z",
    )
    assert win.start.isoformat().startswith("2026-05-01")
    assert win.end.isoformat().startswith("2026-05-15")
    # Calendar-day projections default to Africa/Cairo (UTC+3 in May):
    # 2026-05-15T23:59:59Z is already May 16 on the Cairo wall clock,
    # so the local window spans 16 calendar days.
    assert win.start_date.isoformat() == "2026-05-01"
    assert win.end_date.isoformat() == "2026-05-16"
    assert win.days == 16


def test_calendar_dates_follow_store_timezone() -> None:
    # 22:30 UTC on May 1 is already May 2 in Cairo (UTC+3 in summer).
    win = resolve_date_range_window(
        start_date="2026-05-01T22:30:00Z",
        end_date="2026-05-01T23:30:00Z",
    )
    assert win.tz == "Africa/Cairo"
    assert win.start_date.isoformat() == "2026-05-02"
    assert win.end_date.isoformat() == "2026-05-02"
    assert win.days == 1


def test_explicit_tz_param_is_honored() -> None:
    win = resolve_date_range_window(
        start_date="2026-05-01T22:30:00Z",
        end_date="2026-05-01T23:30:00Z",
        tz="UTC",
    )
    assert win.tz == "UTC"
    assert win.start_date.isoformat() == "2026-05-01"


def test_invalid_tz_falls_back_to_cairo() -> None:
    win = resolve_date_range_window(
        start_date="2026-05-01T00:00:00Z",
        end_date="2026-05-02T00:00:00Z",
        tz="Not/AZone",
    )
    assert win.tz == "Africa/Cairo"


def test_cairo_built_boundaries_project_cleanly() -> None:
    # What the hub sends after the presets fix: Cairo midnight → Cairo
    # end-of-day, as UTC instants. Must project to exactly those local
    # dates with the right day count.
    win = resolve_date_range_window(
        start_date="2026-04-30T21:00:00Z",  # 2026-05-01T00:00 Cairo
        end_date="2026-05-15T20:59:59Z",  # 2026-05-15T23:59:59 Cairo
    )
    assert win.start_date.isoformat() == "2026-05-01"
    assert win.end_date.isoformat() == "2026-05-15"
    assert win.days == 15


def test_naive_datetime_is_assumed_utc() -> None:
    win = resolve_date_range_window(
        start_date="2026-05-01T00:00:00",
        end_date="2026-05-15T23:59:59",
    )
    assert win.start.tzinfo is UTC
    assert win.end.tzinfo is UTC


def test_missing_half_of_pair_raises_422() -> None:
    with pytest.raises(HTTPException) as exc:
        resolve_date_range_window(start_date="2026-05-01T00:00:00Z")
    assert exc.value.status_code == 422

    with pytest.raises(HTTPException) as exc:
        resolve_date_range_window(end_date="2026-05-15T23:59:59Z")
    assert exc.value.status_code == 422


def test_start_after_end_raises_422() -> None:
    with pytest.raises(HTTPException) as exc:
        resolve_date_range_window(
            start_date="2026-05-15T00:00:00Z",
            end_date="2026-05-01T00:00:00Z",
        )
    assert exc.value.status_code == 422


def test_invalid_iso_raises_422() -> None:
    with pytest.raises(HTTPException) as exc:
        resolve_date_range_window(
            start_date="not-a-date",
            end_date="2026-05-15T00:00:00Z",
        )
    assert exc.value.status_code == 422


def test_span_clamping_per_granularity() -> None:
    # Hourly granularity caps at 7 days.
    with pytest.raises(HTTPException) as exc:
        resolve_date_range_window(
            start_date="2026-05-01T00:00:00Z",
            end_date="2026-05-15T00:00:00Z",
            granularity="hour",
        )
    assert exc.value.status_code == 422
    assert "granularity=hour" in exc.value.detail

    # Daily granularity allows up to ~365 days; 14d is fine.
    win = resolve_date_range_window(
        start_date="2026-05-01T00:00:00Z",
        end_date="2026-05-15T00:00:00Z",
        granularity="day",
    )
    assert win.granularity == "day"


def test_z_suffix_and_offset_are_both_accepted() -> None:
    a = resolve_date_range_window(
        start_date="2026-05-01T00:00:00Z",
        end_date="2026-05-15T00:00:00Z",
    )
    b = resolve_date_range_window(
        start_date="2026-05-01T00:00:00+00:00",
        end_date="2026-05-15T00:00:00+00:00",
    )
    assert a.start == b.start
    assert a.end == b.end
