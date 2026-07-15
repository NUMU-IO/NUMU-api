"""Unit tests for the store-timezone resolver — the single definition of
"a day" for analytics (rollup keys, date-range projections, histograms)."""

from datetime import UTC, date, datetime

from src.core.utils.store_timezone import (
    DEFAULT_STORE_TIMEZONE,
    local_date,
    local_day_bounds,
    resolve_store_timezone_name,
    safe_zone,
)


class TestResolveStoreTimezoneName:
    def test_default_when_settings_missing(self):
        assert resolve_store_timezone_name(None) == DEFAULT_STORE_TIMEZONE
        assert resolve_store_timezone_name({}) == DEFAULT_STORE_TIMEZONE

    def test_default_when_blank_or_invalid(self):
        assert resolve_store_timezone_name({"timezone": ""}) == DEFAULT_STORE_TIMEZONE
        assert resolve_store_timezone_name({"timezone": "  "}) == (
            DEFAULT_STORE_TIMEZONE
        )
        assert resolve_store_timezone_name({"timezone": "Not/AZone"}) == (
            DEFAULT_STORE_TIMEZONE
        )
        assert resolve_store_timezone_name({"timezone": 123}) == DEFAULT_STORE_TIMEZONE

    def test_valid_setting_wins(self):
        assert resolve_store_timezone_name({"timezone": "Asia/Riyadh"}) == "Asia/Riyadh"
        assert (
            resolve_store_timezone_name({"timezone": " Africa/Cairo "})
            == "Africa/Cairo"
        )


class TestLocalDayBounds:
    def test_cairo_day_maps_to_shifted_utc_instants(self):
        # Cairo is UTC+3 in July (DST) — local midnight is 21:00 UTC the
        # previous evening.
        start, end = local_day_bounds(date(2026, 7, 13), "Africa/Cairo")
        assert start == datetime(2026, 7, 12, 21, 0, tzinfo=UTC)
        assert end == datetime(2026, 7, 13, 21, 0, tzinfo=UTC)

    def test_half_open_window_is_exactly_24h(self):
        start, end = local_day_bounds(date(2026, 2, 1), "Africa/Cairo")
        assert (end - start).total_seconds() == 86400

    def test_utc_zone_is_identity(self):
        start, end = local_day_bounds(date(2026, 7, 13), "UTC")
        assert start == datetime(2026, 7, 13, 0, 0, tzinfo=UTC)
        assert end == datetime(2026, 7, 14, 0, 0, tzinfo=UTC)


class TestLocalDate:
    def test_late_utc_evening_is_next_cairo_day(self):
        # The original bug: a 22:30 UTC order displayed on the previous
        # Cairo day. 22:30 UTC = 01:30 Cairo next day in July.
        instant = datetime(2026, 7, 12, 22, 30, tzinfo=UTC)
        assert local_date(instant, "Africa/Cairo") == date(2026, 7, 13)

    def test_naive_datetime_assumed_utc(self):
        instant = datetime(2026, 7, 12, 22, 30)
        assert local_date(instant, "Africa/Cairo") == date(2026, 7, 13)


class TestSafeZone:
    def test_falls_back_on_garbage(self):
        assert str(safe_zone("Not/AZone")) == DEFAULT_STORE_TIMEZONE
        assert str(safe_zone(None)) == DEFAULT_STORE_TIMEZONE
        assert str(safe_zone("Asia/Riyadh")) == "Asia/Riyadh"
