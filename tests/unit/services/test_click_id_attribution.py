"""Click-id → traffic-source derivation.

The regression these guard: a shopper with an old Meta click in the
90-day attribution cookie who then arrives from a TikTok ad (``ttclid``
only, no UTMs) must be attributed to TikTok, not Meta.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from src.application.services.click_id_attribution import (
    DERIVED_MEDIUM,
    derive_source_from_click_ids,
    effective_utm_source_medium,
)
from src.core.entities.attribution import AttributionTouch


def _touch(**overrides) -> AttributionTouch:
    base = {"ts": datetime(2026, 8, 21, tzinfo=UTC)}
    base.update(overrides)
    return AttributionTouch(**base)


class TestDeriveSourceFromClickIds:
    def test_none_touch(self):
        assert derive_source_from_click_ids(None) is None

    def test_no_click_ids(self):
        assert derive_source_from_click_ids(_touch(utm_source="newsletter")) is None

    @pytest.mark.parametrize(
        ("field", "expected"),
        [("ttclid", "tiktok"), ("fbclid", "facebook"), ("gclid", "google")],
    )
    def test_single_click_id(self, field: str, expected: str):
        assert derive_source_from_click_ids(_touch(**{field: "abc123"})) == expected

    def test_whitespace_click_id_ignored(self):
        assert derive_source_from_click_ids(_touch(fbclid="   ")) is None

    def test_dict_touch(self):
        assert derive_source_from_click_ids({"ttclid": "E.C.P.x"}) == "tiktok"

    def test_entity_accepts_ttclid(self):
        # The envelope field itself — a TikTok landing now produces a touch.
        assert _touch(ttclid="E.C.P.x").ttclid == "E.C.P.x"


class TestEffectiveUtmSourceMedium:
    def test_explicit_source_wins(self):
        touch = _touch(ttclid="E.C.P.x")
        assert effective_utm_source_medium(touch, "ig", "social") == ("ig", "social")

    def test_tiktok_click_without_utms(self):
        touch = _touch(ttclid="E.C.P.x")
        assert effective_utm_source_medium(touch, None, None) == (
            "tiktok",
            DERIVED_MEDIUM,
        )

    def test_supplied_medium_kept(self):
        touch = _touch(fbclid="IwAR0xyz")
        assert effective_utm_source_medium(touch, None, "cpc") == ("facebook", "cpc")

    def test_no_signal_passes_through(self):
        assert effective_utm_source_medium(_touch(), None, None) == (None, None)
        assert effective_utm_source_medium(None, None, None) == (None, None)

    def test_tiktok_touch_is_not_facebook(self):
        # The bug: a fresh TikTok touch must not inherit Meta attribution.
        touch = _touch(ttclid="E.C.P.x", fbclid=None)
        assert effective_utm_source_medium(touch, None, None)[0] == "tiktok"
