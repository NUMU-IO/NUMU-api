"""Unit tests for the TikTok tracking request schemas — validation contracts.

Pins the TikTok deltas vs Meta: pixel IDs are ALPHANUMERIC (not 15-16 digits),
test-event codes are alphanumeric (looser than Meta's ^TEST\\d+$).
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from src.api.v1.schemas.tenant.tracking import (
    SaveTikTokTrackingRequest,
    SendTikTokTestEventRequest,
    TikTokPixelEntry,
)


class TestPixelIdValidation:
    def test_alphanumeric_pixel_accepted(self):
        r = SaveTikTokTrackingRequest(
            pixel_id="C4A2B1D3E4F5G6", pixel_enabled=True, api_enabled=False
        )
        assert r.pixel_id == "C4A2B1D3E4F5G6"

    def test_all_digits_still_valid(self):
        # Alphanumeric includes digits — a numeric-looking code is fine.
        r = SaveTikTokTrackingRequest(
            pixel_id="1234567890", pixel_enabled=True, api_enabled=False
        )
        assert r.pixel_id == "1234567890"

    def test_too_short_rejected(self):
        with pytest.raises(ValidationError):
            SaveTikTokTrackingRequest(
                pixel_id="ab12", pixel_enabled=True, api_enabled=False
            )

    def test_symbols_rejected(self):
        with pytest.raises(ValidationError):
            SaveTikTokTrackingRequest(
                pixel_id="C4A2-B1!!", pixel_enabled=True, api_enabled=False
            )


class TestTokenGateShape:
    def test_api_token_optional(self):
        # Schema allows omitting the token; the ROUTE enforces the 422 when
        # api_enabled and no token is on file — not the schema.
        r = SaveTikTokTrackingRequest(
            pixel_id="C4A2B1", pixel_enabled=False, api_enabled=True
        )
        assert r.api_access_token is None

    def test_short_token_rejected(self):
        with pytest.raises(ValidationError):
            SaveTikTokTrackingRequest(
                pixel_id="C4A2B1",
                pixel_enabled=False,
                api_enabled=True,
                api_access_token="short",
            )


class TestTestEventCode:
    def test_alphanumeric_dash_underscore_ok(self):
        r = SendTikTokTestEventRequest(test_event_code="TEST_123-ab")
        assert r.test_event_code == "TEST_123-ab"

    def test_spaces_rejected(self):
        with pytest.raises(ValidationError):
            SendTikTokTestEventRequest(test_event_code="has space")

    def test_save_blank_test_code_normalizes_to_none(self):
        r = SaveTikTokTrackingRequest(
            pixel_id="C4A2B1",
            pixel_enabled=True,
            api_enabled=False,
            test_event_code="",
        )
        assert r.test_event_code is None


class TestPixelEntry:
    def test_valid_entry(self):
        e = TikTokPixelEntry(pixel_id="AAA111", pixel_enabled=True, api_enabled=False)
        assert e.pixel_id == "AAA111"
        assert e.api_enabled is False

    def test_entry_rejects_bad_pixel(self):
        with pytest.raises(ValidationError):
            TikTokPixelEntry(pixel_id="!!", pixel_enabled=True, api_enabled=True)
