"""Unit tests for ``resolve_mode`` — the Meta tracking activation
mode resolver.

Covers all four outcomes (``off``, ``pixel_only``, ``capi_only``,
``both``) plus the edge cases that the dashboard relies on (missing
pixel_id falls back to off; toggling either flag preserves the other;
saving capi_enabled without a token is treated as off).
"""

import pytest

from src.application.services.meta_tracking_resolver import resolve_mode

# ---------------------------------------------------------------------------
# All four outcomes
# ---------------------------------------------------------------------------


class TestFourModes:
    """The truth table that drives the dashboard's mode selector."""

    def test_off_when_nothing_configured(self):
        assert resolve_mode({}, has_capi_token=False) == "off"

    def test_off_when_meta_cfg_is_none(self):
        # Stores that have never touched the Meta panel pass None.
        assert resolve_mode(None, has_capi_token=False) == "off"

    def test_pixel_only_when_pixel_enabled_and_no_capi(self):
        cfg = {
            "pixel_id": "123456789012345",
            "pixel_enabled": True,
            "capi_enabled": False,
        }
        assert resolve_mode(cfg, has_capi_token=False) == "pixel_only"

    def test_capi_only_when_capi_enabled_with_token(self):
        cfg = {
            "pixel_id": "123456789012345",
            "pixel_enabled": False,
            "capi_enabled": True,
        }
        assert resolve_mode(cfg, has_capi_token=True) == "capi_only"

    def test_both_when_pixel_and_capi_active(self):
        cfg = {
            "pixel_id": "123456789012345",
            "pixel_enabled": True,
            "capi_enabled": True,
        }
        assert resolve_mode(cfg, has_capi_token=True) == "both"


# ---------------------------------------------------------------------------
# Activation gates / edge cases
# ---------------------------------------------------------------------------


class TestActivationGates:
    """Each gate (pixel_id present, has_token, individual flags) must
    independently veto its channel."""

    def test_capi_enabled_without_token_falls_back_to_off(self):
        # Saving the booleans without an actual ServiceCredential row
        # is treated as off — the settings PUT route should reject this
        # before persisting, but the resolver is the last line of
        # defense.
        cfg = {
            "pixel_id": "123456789012345",
            "pixel_enabled": False,
            "capi_enabled": True,
        }
        assert resolve_mode(cfg, has_capi_token=False) == "off"

    def test_capi_enabled_without_token_still_allows_pixel_mode(self):
        # Mode A merchant who toggled capi_enabled on but never finished
        # uploading a token should still get Pixel events firing.
        cfg = {
            "pixel_id": "123456789012345",
            "pixel_enabled": True,
            "capi_enabled": True,  # missing token below downgrades capi side
        }
        assert resolve_mode(cfg, has_capi_token=False) == "pixel_only"

    def test_no_pixel_id_means_off_regardless_of_flags(self):
        # CAPI POSTs go to /v21.0/{pixel_id}/events — no pixel_id, no events.
        cfg = {
            "pixel_id": None,
            "pixel_enabled": True,
            "capi_enabled": True,
        }
        assert resolve_mode(cfg, has_capi_token=True) == "off"

    def test_empty_pixel_id_means_off(self):
        cfg = {
            "pixel_id": "",
            "pixel_enabled": True,
            "capi_enabled": True,
        }
        assert resolve_mode(cfg, has_capi_token=True) == "off"

    def test_missing_pixel_enabled_defaults_off(self):
        # The plan persists booleans explicitly — but legacy / partial
        # configs may omit them. Default behavior is "off for that
        # channel" rather than "leak a fire".
        cfg = {"pixel_id": "123456789012345"}
        assert resolve_mode(cfg, has_capi_token=True) == "off"

    @pytest.mark.parametrize(
        "pixel_enabled,capi_enabled,has_token,expected",
        [
            (False, False, False, "off"),
            (False, False, True, "off"),
            (True, False, False, "pixel_only"),
            (True, False, True, "pixel_only"),
            (False, True, False, "off"),  # capi without token → off
            (False, True, True, "capi_only"),
            (True, True, False, "pixel_only"),  # capi without token → pixel only
            (True, True, True, "both"),
        ],
    )
    def test_full_truth_table(self, pixel_enabled, capi_enabled, has_token, expected):
        """Exhaustive truth table: every combination of three booleans."""
        cfg = {
            "pixel_id": "123456789012345",
            "pixel_enabled": pixel_enabled,
            "capi_enabled": capi_enabled,
        }
        assert resolve_mode(cfg, has_capi_token=has_token) == expected
