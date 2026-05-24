"""Tests for the UA → device classifier (feature 002 US3)."""

from __future__ import annotations

import pytest

from src.application.services.device_classifier import classify


@pytest.mark.parametrize(
    ("user_agent", "expected"),
    [
        # Empty / null
        (None, None),
        ("", None),
        ("   ", None),
        # iOS phones
        (
            "Mozilla/5.0 (iPhone; CPU iPhone OS 17_2 like Mac OS X) "
            "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.2 Mobile/15E148 Safari/604.1",
            "mobile",
        ),
        # iPad
        (
            "Mozilla/5.0 (iPad; CPU OS 17_2 like Mac OS X) "
            "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.2 Mobile/15E148 Safari/604.1",
            "tablet",
        ),
        # Android phone (Pixel)
        (
            "Mozilla/5.0 (Linux; Android 14; Pixel 8) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.6099.230 Mobile Safari/537.36",
            "mobile",
        ),
        # Android tablet (no "Mobile" token)
        (
            "Mozilla/5.0 (Linux; Android 14; SM-X510) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.6099.230 Safari/537.36",
            "tablet",
        ),
        # Desktop Chrome on Windows
        (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.6099.230 Safari/537.36",
            "desktop",
        ),
        # Desktop Safari on Mac
        (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_2) "
            "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.2 Safari/605.1.15",
            "desktop",
        ),
        # curl — collapses into desktop (rare on storefront)
        ("curl/8.4.0", "desktop"),
        # Kindle Fire
        (
            "Mozilla/5.0 (Linux; U; Android 11; en-us; KFTRWI Build/RS8112.1858N) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Silk/108.5.1 like Chrome/108.0.5359.128 Safari/537.36",
            "tablet",
        ),
    ],
)
def test_classify(user_agent: str | None, expected: str | None) -> None:
    assert classify(user_agent) == expected
