"""Unit tests for ``resolve_tiktok_pixels`` — sibling of the Meta resolver.

Pins the back-compat contract (legacy single pixel_id → 1-element list) and
multi-pixel fan-out filtered by ``mode="api"`` (TikTok's server toggle).
"""

from __future__ import annotations

from src.application.services.tiktok_pixel_resolver import (
    ResolvedTikTokPixel,
    resolve_tiktok_pixels,
)


class TestBackCompat:
    def test_none_and_empty_are_empty(self):
        assert resolve_tiktok_pixels(None) == []
        assert resolve_tiktok_pixels({}) == []

    def test_legacy_single_pixel(self):
        out = resolve_tiktok_pixels({
            "pixel_id": "C4A2B1D3E4",
            "pixel_enabled": True,
            "api_enabled": True,
        })
        assert out == [
            ResolvedTikTokPixel(
                pixel_id="C4A2B1D3E4",
                pixel_enabled=True,
                api_enabled=True,
                label="Primary",
                role="primary",
            )
        ]

    def test_api_mode_excludes_disabled_legacy(self):
        assert (
            resolve_tiktok_pixels(
                {"pixel_id": "C4A2B1D3E4", "pixel_enabled": True, "api_enabled": False},
                mode="api",
            )
            == []
        )

    def test_no_pixel_id_no_entries(self):
        assert resolve_tiktok_pixels({"api_enabled": True}) == []


class TestMultiPixel:
    def test_two_pixels_fan_out(self):
        out = resolve_tiktok_pixels({
            "pixels": [{"pixel_id": "AAA111"}, {"pixel_id": "BBB222"}]
        })
        assert [p.pixel_id for p in out] == ["AAA111", "BBB222"]

    def test_pixels_array_overrides_legacy(self):
        out = resolve_tiktok_pixels({
            "pixel_id": "LEGACY9",
            "api_enabled": True,
            "pixels": [{"pixel_id": "AAA111"}],
        })
        assert [p.pixel_id for p in out] == ["AAA111"]

    def test_api_mode_filters_disabled(self):
        out = resolve_tiktok_pixels(
            {
                "pixels": [
                    {"pixel_id": "AAA111", "api_enabled": True},
                    {"pixel_id": "BBB222", "api_enabled": False},
                    {"pixel_id": "CCC333", "api_enabled": True},
                ]
            },
            mode="api",
        )
        assert [p.pixel_id for p in out] == ["AAA111", "CCC333"]

    def test_default_flags_enabled(self):
        out = resolve_tiktok_pixels({"pixels": [{"pixel_id": "AAA111"}]})
        assert out[0].pixel_enabled is True
        assert out[0].api_enabled is True

    def test_skips_malformed_entries(self):
        out = resolve_tiktok_pixels({
            "pixels": [{"pixel_id": "AAA111"}, {}, "nope", {"pixel_id": "CCC333"}]
        })
        assert [p.pixel_id for p in out] == ["AAA111", "CCC333"]

    def test_empty_pixels_falls_through_to_legacy(self):
        out = resolve_tiktok_pixels({
            "pixels": [],
            "pixel_id": "LEG9",
            "api_enabled": True,
        })
        assert [p.pixel_id for p in out] == ["LEG9"]
