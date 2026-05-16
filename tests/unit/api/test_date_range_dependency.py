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
