"""Unit tests for metric-target period bounds — the calendar math behind
the goal gauges. Month and quarter windows must be exact calendar
periods on the store's wall clock, with correct year rollover."""

from datetime import date, datetime

from src.api.v1.routes.stores.analytics import _period_bounds


class TestMonthBounds:
    def test_mid_month(self):
        start, end = _period_bounds(datetime(2026, 7, 14, 10, 30), "month")
        assert start == date(2026, 7, 1)
        assert end == date(2026, 8, 1)  # exclusive
        assert (end - start).days == 31

    def test_february_non_leap(self):
        start, end = _period_bounds(datetime(2026, 2, 10), "month")
        assert start == date(2026, 2, 1)
        assert end == date(2026, 3, 1)
        assert (end - start).days == 28

    def test_december_rolls_to_next_year(self):
        start, end = _period_bounds(datetime(2026, 12, 20), "month")
        assert start == date(2026, 12, 1)
        assert end == date(2027, 1, 1)


class TestQuarterBounds:
    def test_q1(self):
        for m in (1, 2, 3):
            start, end = _period_bounds(datetime(2026, m, 15), "quarter")
            assert start == date(2026, 1, 1)
            assert end == date(2026, 4, 1)

    def test_q3(self):
        start, end = _period_bounds(datetime(2026, 7, 14), "quarter")
        assert start == date(2026, 7, 1)
        assert end == date(2026, 10, 1)

    def test_q4_rolls_to_next_year(self):
        for m in (10, 11, 12):
            start, end = _period_bounds(datetime(2026, m, 5), "quarter")
            assert start == date(2026, 10, 1)
            assert end == date(2027, 1, 1)

    def test_quarter_boundaries_are_exactly_three_months(self):
        for m in (1, 4, 7, 10):
            start, end = _period_bounds(datetime(2026, m, 1), "quarter")
            assert start.month == m
            assert (end.year - start.year) * 12 + (end.month - start.month) == 3
