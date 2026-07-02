"""Unit tests for ``resolve_tiktok_mode`` — TikTok activation-mode resolver.

Same four-outcome truth table as Meta, keyed on ``api_enabled`` instead of
``capi_enabled``. The ``capi_only`` literal is reused for TikTok's
Events-API-only mode (shared TrackingMode type).
"""

from __future__ import annotations

import pytest

from src.application.services.tiktok_tracking_resolver import resolve_tiktok_mode


class TestFourModes:
    def test_off_when_nothing(self):
        assert resolve_tiktok_mode({}, has_api_token=False) == "off"
        assert resolve_tiktok_mode(None, has_api_token=False) == "off"

    def test_pixel_only(self):
        cfg = {"pixel_id": "C4A2B1", "pixel_enabled": True, "api_enabled": False}
        assert resolve_tiktok_mode(cfg, has_api_token=False) == "pixel_only"

    def test_api_only(self):
        cfg = {"pixel_id": "C4A2B1", "pixel_enabled": False, "api_enabled": True}
        assert resolve_tiktok_mode(cfg, has_api_token=True) == "capi_only"

    def test_both(self):
        cfg = {"pixel_id": "C4A2B1", "pixel_enabled": True, "api_enabled": True}
        assert resolve_tiktok_mode(cfg, has_api_token=True) == "both"


class TestGates:
    def test_api_enabled_without_token_is_off(self):
        cfg = {"pixel_id": "C4A2B1", "pixel_enabled": False, "api_enabled": True}
        assert resolve_tiktok_mode(cfg, has_api_token=False) == "off"

    def test_api_without_token_still_allows_pixel(self):
        cfg = {"pixel_id": "C4A2B1", "pixel_enabled": True, "api_enabled": True}
        assert resolve_tiktok_mode(cfg, has_api_token=False) == "pixel_only"

    def test_no_pixel_id_is_off(self):
        cfg = {"pixel_id": None, "pixel_enabled": True, "api_enabled": True}
        assert resolve_tiktok_mode(cfg, has_api_token=True) == "off"

    @pytest.mark.parametrize(
        "pixel_enabled,api_enabled,has_token,expected",
        [
            (False, False, False, "off"),
            (True, False, False, "pixel_only"),
            (False, True, False, "off"),
            (False, True, True, "capi_only"),
            (True, True, False, "pixel_only"),
            (True, True, True, "both"),
        ],
    )
    def test_truth_table(self, pixel_enabled, api_enabled, has_token, expected):
        cfg = {
            "pixel_id": "C4A2B1",
            "pixel_enabled": pixel_enabled,
            "api_enabled": api_enabled,
        }
        assert resolve_tiktok_mode(cfg, has_api_token=has_token) == expected
