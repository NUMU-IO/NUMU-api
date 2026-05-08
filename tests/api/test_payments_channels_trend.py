"""Tests for backend-019 payment-channel trend computation.

The dashboard previously rendered ``trend: "stable"`` for every
channel because the schema default was wired through unchanged.
This sprint slice replaces that with a real current-vs-prior
period comparison. Pin the bucketization so the dashboard's
arrow-direction stays predictable.
"""

from __future__ import annotations

import pytest

from src.api.v1.routes.shopify.payments import (
    TREND_DELTA_PCT_THRESHOLD,
    _success_rate,
    compute_trend,
)


class TestComputeTrend:
    @pytest.mark.parametrize(
        "current,prior,expected",
        [
            (95.0, 80.0, "up"),  # +15 → up
            (85.0, 80.0, "stable"),  # +5 → still inside band
            (80.0, 80.0, "stable"),  # equal
            (75.1, 80.0, "stable"),  # -4.9 → just inside band
            (74.9, 80.0, "down"),  # -5.1 → down
            (60.0, 90.0, "down"),  # big drop
            (10.0, 0.0, "up"),  # noise to traffic
        ],
    )
    def test_buckets(self, current, prior, expected):
        assert compute_trend(current_rate=current, prior_rate=prior) == expected

    def test_threshold_is_5_percent_locked(self):
        """The Shopify-app dashboard's arrow indicators use this exact
        threshold to render up/down/stable. Changing it without
        coordination causes a visible UX shift."""
        assert TREND_DELTA_PCT_THRESHOLD == 5.0


class TestSuccessRateLookup:
    def test_existing_channel_returns_rate(self):
        rows = [
            {
                "channel": "cod",
                "gateway": "cod",
                "total_attempts": 200,
                "successful_raw": 160,
            },
        ]
        assert _success_rate(rows, "cod", "cod") == 80.0

    def test_missing_channel_returns_zero(self):
        rows = [
            {
                "channel": "cod",
                "gateway": "cod",
                "total_attempts": 100,
                "successful_raw": 80,
            },
        ]
        assert _success_rate(rows, "card", "paymob") == 0.0

    def test_zero_attempts_returns_zero(self):
        rows = [
            {
                "channel": "cod",
                "gateway": "cod",
                "total_attempts": 0,
                "successful_raw": 0,
            },
        ]
        assert _success_rate(rows, "cod", "cod") == 0.0

    def test_channel_match_requires_both_channel_and_gateway(self):
        """Critical: the same channel name can have multiple gateways
        (e.g. 'card' via paymob vs 'card' via stripe). Matching on
        channel alone would incorrectly aggregate them. Pin both."""
        rows = [
            {
                "channel": "card",
                "gateway": "paymob",
                "total_attempts": 100,
                "successful_raw": 90,
            },
            {
                "channel": "card",
                "gateway": "stripe",
                "total_attempts": 50,
                "successful_raw": 40,
            },
        ]
        assert _success_rate(rows, "card", "paymob") == 90.0
        assert _success_rate(rows, "card", "stripe") == 80.0
