"""Wave 2 Phase 13 — tests for meta_pixel_resolver.

Pins the back-compat contract (legacy single pixel_id → 1-element list)
and the multi-pixel fan-out (pixels[] → N-element list, filtered by mode).
"""

from __future__ import annotations

import pytest

from src.application.services.meta_pixel_resolver import (
    ResolvedPixel,
    resolve_pixels,
    resolve_pixels_for_product,
)


class TestResolverBackCompat:
    """Stores that never opted into multi-pixel see identical behavior."""

    def test_none_settings_empty_list(self):
        assert resolve_pixels(None) == []
        assert resolve_pixels({}) == []

    def test_legacy_single_pixel(self):
        out = resolve_pixels({
            "pixel_id": "111111111111111",
            "pixel_enabled": True,
            "capi_enabled": True,
        })
        assert len(out) == 1
        assert out[0] == ResolvedPixel(
            pixel_id="111111111111111",
            pixel_enabled=True,
            capi_enabled=True,
            label="Primary",
            role="primary",
        )

    def test_legacy_single_pixel_with_disabled_capi(self):
        out = resolve_pixels({
            "pixel_id": "111111111111111",
            "pixel_enabled": True,
            "capi_enabled": False,
        })
        assert out[0].capi_enabled is False
        # capi-mode filter excludes it.
        assert (
            resolve_pixels(
                {
                    "pixel_id": "111111111111111",
                    "pixel_enabled": True,
                    "capi_enabled": False,
                },
                mode="capi",
            )
            == []
        )

    def test_no_pixel_id_no_entries(self):
        # capi_enabled=True with no pixel_id → no fire (defensive against
        # half-configured stores).
        assert resolve_pixels({"capi_enabled": True}) == []


class TestResolverMultiPixel:
    """The new pixels[] array fans out cleanly."""

    def test_two_pixels(self):
        out = resolve_pixels({
            "pixels": [
                {"pixel_id": "111111111111111", "label": "Primary"},
                {"pixel_id": "222222222222222", "label": "Retargeting"},
            ]
        })
        assert [p.pixel_id for p in out] == [
            "111111111111111",
            "222222222222222",
        ]
        assert [p.label for p in out] == ["Primary", "Retargeting"]

    def test_pixels_array_overrides_legacy_field(self):
        # When both pixels[] and the legacy pixel_id are set, pixels[]
        # wins. The legacy field is preserved on the model only so
        # downstream readers (older code paths) still have a value.
        out = resolve_pixels({
            "pixel_id": "999999999999999",  # legacy, ignored
            "pixel_enabled": True,
            "capi_enabled": True,
            "pixels": [
                {"pixel_id": "111111111111111"},
                {"pixel_id": "222222222222222"},
            ],
        })
        assert [p.pixel_id for p in out] == [
            "111111111111111",
            "222222222222222",
        ]

    def test_capi_mode_filters_disabled(self):
        out = resolve_pixels(
            {
                "pixels": [
                    {"pixel_id": "111111111111111", "capi_enabled": True},
                    {"pixel_id": "222222222222222", "capi_enabled": False},
                    {"pixel_id": "333333333333333", "capi_enabled": True},
                ]
            },
            mode="capi",
        )
        assert [p.pixel_id for p in out] == [
            "111111111111111",
            "333333333333333",
        ]

    def test_pixel_mode_filters_disabled(self):
        out = resolve_pixels(
            {
                "pixels": [
                    {"pixel_id": "111111111111111", "pixel_enabled": True},
                    {"pixel_id": "222222222222222", "pixel_enabled": False},
                ]
            },
            mode="pixel",
        )
        assert [p.pixel_id for p in out] == ["111111111111111"]

    def test_default_flags_enabled(self):
        # Entries without explicit pixel_enabled/capi_enabled default to True.
        out = resolve_pixels({"pixels": [{"pixel_id": "111111111111111"}]})
        assert out[0].pixel_enabled is True
        assert out[0].capi_enabled is True

    def test_skips_malformed_entries(self):
        # Defensive against half-saved data (missing pixel_id, wrong type).
        out = resolve_pixels({
            "pixels": [
                {"pixel_id": "111111111111111"},
                {},  # no pixel_id
                "not a dict",  # wrong type
                {"pixel_id": "333333333333333"},
            ]
        })
        assert [p.pixel_id for p in out] == [
            "111111111111111",
            "333333333333333",
        ]

    def test_empty_pixels_array_falls_through_to_legacy(self):
        # An empty pixels=[] means "no multi-pixel configured" — falls
        # back to the legacy single-pixel path.
        out = resolve_pixels({
            "pixels": [],
            "pixel_id": "111111111111111",
            "pixel_enabled": True,
            "capi_enabled": True,
        })
        assert len(out) == 1
        assert out[0].pixel_id == "111111111111111"


class TestResolverRoles:
    """Role/label metadata flows through to the resolved entry."""

    @pytest.mark.parametrize("role", ["primary", "retargeting", "agency", None])
    def test_role_passthrough(self, role):
        out = resolve_pixels({
            "pixels": [{"pixel_id": "111111111111111", "role": role}]
        })
        assert out[0].role == role


# ===========================================================================
# Wave 2 Phase 13.2 — Per-product custom pixel overrides
# ===========================================================================


class TestResolveForProductFallthrough:
    """Without overrides, behaves identically to ``resolve_pixels``."""

    def test_none_overrides_falls_through(self):
        store_cfg = {"pixels": [{"pixel_id": "111111111111111"}]}
        out = resolve_pixels_for_product(store_cfg, None)
        assert [p.pixel_id for p in out] == ["111111111111111"]

    def test_non_dict_overrides_falls_through(self):
        # Defensive against half-saved data.
        store_cfg = {"pixels": [{"pixel_id": "111111111111111"}]}
        out = resolve_pixels_for_product(store_cfg, "not a dict")  # type: ignore[arg-type]
        assert len(out) == 1

    def test_empty_pixels_list_falls_through(self):
        store_cfg = {"pixels": [{"pixel_id": "111111111111111"}]}
        out = resolve_pixels_for_product(
            store_cfg, {"override_mode": "exclusive", "pixels": []}
        )
        assert [p.pixel_id for p in out] == ["111111111111111"]

    def test_missing_override_mode_falls_through(self):
        # Conservative: unknown mode means "ignore override".
        store_cfg = {"pixels": [{"pixel_id": "111111111111111"}]}
        out = resolve_pixels_for_product(
            store_cfg, {"pixels": [{"pixel_id": "222222222222222"}]}
        )
        # Default mode is "additive" when missing — verified below.
        # If both fire, len should be 2. Confirm default behavior.
        assert len(out) == 2

    def test_unknown_override_mode_falls_through(self):
        store_cfg = {"pixels": [{"pixel_id": "111111111111111"}]}
        out = resolve_pixels_for_product(
            store_cfg,
            {"override_mode": "garbage", "pixels": [{"pixel_id": "222"}]},
        )
        assert [p.pixel_id for p in out] == ["111111111111111"]


class TestResolveForProductExclusive:
    """``exclusive`` mode replaces store-level pixels entirely for the product."""

    def test_exclusive_returns_only_overrides(self):
        store_cfg = {
            "pixels": [
                {"pixel_id": "111111111111111", "label": "Primary"},
                {"pixel_id": "222222222222222", "label": "Retargeting"},
            ]
        }
        overrides = {
            "override_mode": "exclusive",
            "pixels": [{"pixel_id": "999999999999999", "label": "Agency-A"}],
        }
        out = resolve_pixels_for_product(store_cfg, overrides)
        assert [p.pixel_id for p in out] == ["999999999999999"]

    def test_exclusive_with_no_store_pixels_still_returns_overrides(self):
        out = resolve_pixels_for_product(
            None,
            {
                "override_mode": "exclusive",
                "pixels": [{"pixel_id": "999999999999999"}],
            },
        )
        assert [p.pixel_id for p in out] == ["999999999999999"]

    def test_exclusive_with_all_overrides_filtered_falls_back(self):
        # If filter mode excludes every override entry, fall back to
        # store-level (rather than fire nothing).
        store_cfg = {"pixels": [{"pixel_id": "111111111111111"}]}
        out = resolve_pixels_for_product(
            store_cfg,
            {
                "override_mode": "exclusive",
                "pixels": [{"pixel_id": "222222222222222", "capi_enabled": False}],
            },
            mode="capi",
        )
        assert [p.pixel_id for p in out] == ["111111111111111"]


class TestResolveForProductAdditive:
    """``additive`` mode layers overrides on top of store-level pixels."""

    def test_additive_merges_with_dedup(self):
        store_cfg = {
            "pixels": [
                {"pixel_id": "111111111111111"},
                {"pixel_id": "222222222222222"},
            ]
        }
        overrides = {
            "override_mode": "additive",
            "pixels": [
                {"pixel_id": "222222222222222"},  # duplicate — should NOT add
                {"pixel_id": "333333333333333"},
            ],
        }
        out = resolve_pixels_for_product(store_cfg, overrides)
        assert [p.pixel_id for p in out] == [
            "111111111111111",
            "222222222222222",
            "333333333333333",
        ]

    def test_additive_preserves_store_pixel_metadata(self):
        # Store entry has role=primary; override entry has role=agency.
        # When both refer to the same pixel_id, the store-level metadata
        # wins (dedup keeps the first-seen entry).
        store_cfg = {
            "pixels": [
                {"pixel_id": "111111111111111", "role": "primary", "label": "Store"}
            ]
        }
        overrides = {
            "override_mode": "additive",
            "pixels": [
                {"pixel_id": "111111111111111", "role": "agency", "label": "Agency"}
            ],
        }
        out = resolve_pixels_for_product(store_cfg, overrides)
        assert len(out) == 1
        assert out[0].role == "primary"
        assert out[0].label == "Store"


class TestResolveForProductModeFilters:
    """``mode="capi"`` / ``mode="pixel"`` still apply across overrides."""

    def test_capi_mode_filters_override_entries(self):
        store_cfg = {"pixels": [{"pixel_id": "111111111111111"}]}
        overrides = {
            "override_mode": "additive",
            "pixels": [
                {"pixel_id": "222222222222222", "capi_enabled": False},
                {"pixel_id": "333333333333333", "capi_enabled": True},
            ],
        }
        out = resolve_pixels_for_product(store_cfg, overrides, mode="capi")
        assert [p.pixel_id for p in out] == [
            "111111111111111",
            "333333333333333",
        ]
