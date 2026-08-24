"""Traffic-source derivation chain.

Two regressions these guard:

  1. A shopper with an old Meta click in the 90-day attribution cookie who
     then arrives from a TikTok ad (``ttclid`` only, no UTMs) must be
     attributed to TikTok, not Meta.
  2. An untagged organic visit must not fall through to "Direct" when the
     in-app browser or the referrer already says which platform it came
     from — the case that hid 268 TikTok and 229 Instagram visits.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from src.application.services.traffic_source import (
    DERIVED_MEDIUM,
    MEDIUM_ORGANIC,
    MEDIUM_SOCIAL,
    derive_source_from_click_ids,
    derive_source_from_referrer,
    derive_source_from_user_agent,
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


# Real User-Agent fragments, trimmed to the vendor tokens that matter.
_TIKTOK_UA = (
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_5 like Mac OS X) AppleWebKit/605.1.15 "
    "(KHTML, like Gecko) Mobile/15E148 BytedanceWebview/d8a21c6 musical_ly_34.5.0"
)
_INSTAGRAM_UA = (
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_5 like Mac OS X) AppleWebKit/605.1.15 "
    "Instagram 339.0.0.30.105 (iPhone14,5; iOS 17_5)"
)
_PLAIN_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/127.0 Safari/537.36"
)


class TestDeriveSourceFromUserAgent:
    def test_tiktok_webview(self):
        assert derive_source_from_user_agent(_TIKTOK_UA) == ("tiktok", MEDIUM_SOCIAL)

    def test_instagram_webview(self):
        assert derive_source_from_user_agent(_INSTAGRAM_UA) == (
            "instagram",
            MEDIUM_SOCIAL,
        )

    def test_instagram_beats_meta_tokens(self):
        # Instagram's webview reports FBAV too; the more specific app wins.
        ua = _INSTAGRAM_UA + " [FBAN/FBIOS;FBAV/450.0.0]"
        assert derive_source_from_user_agent(ua)[0] == "instagram"

    def test_plain_browser_is_not_a_platform(self):
        assert derive_source_from_user_agent(_PLAIN_UA) is None

    def test_empty(self):
        assert derive_source_from_user_agent(None) is None
        assert derive_source_from_user_agent("") is None


class TestDeriveSourceFromReferrer:
    @pytest.mark.parametrize(
        ("referrer", "expected"),
        [
            ("https://l.instagram.com/", "instagram"),
            ("https://instagram.com/", "instagram"),
            ("https://l.facebook.com/", "facebook"),
            ("https://www.tiktok.com/@shop", "tiktok"),
            ("https://linktr.ee/vionne", "linktree"),
            ("https://www.google.com/", "google"),
            ("https://google.com.eg/search?q=x", "google"),
            ("https://www.bing.com/", "bing"),
        ],
    )
    def test_known_hosts(self, referrer: str, expected: str):
        result = derive_source_from_referrer(referrer)
        assert result is not None and result[0] == expected

    def test_search_medium_is_organic(self):
        assert derive_source_from_referrer("https://www.google.com/")[1] == (
            MEDIUM_ORGANIC
        )

    def test_gmail_is_email_not_search(self):
        # `com.google.android.gm` would otherwise match the Google pattern.
        assert derive_source_from_referrer("android-app://com.google.android.gm/") == (
            "gmail",
            "email",
        )

    def test_own_domain_derives_nothing(self):
        # Internal navigation must never invent a source.
        assert derive_source_from_referrer("https://vionne.numueg.app/products") is None

    def test_bare_host_without_scheme(self):
        assert derive_source_from_referrer("l.instagram.com")[0] == "instagram"

    def test_empty(self):
        assert derive_source_from_referrer(None) is None
        assert derive_source_from_referrer("   ") is None


class TestFullChainPrecedence:
    def test_explicit_source_beats_everything(self):
        assert effective_utm_source_medium(
            _touch(ttclid="E.C.P.x"),
            "newsletter",
            "email",
            referrer="https://l.instagram.com/",
            user_agent=_TIKTOK_UA,
        ) == ("newsletter", "email")

    def test_click_id_beats_user_agent(self):
        # Inside the TikTok webview but arriving on a Meta ad click: the
        # click id is the stronger, more specific signal.
        source, medium = effective_utm_source_medium(
            _touch(fbclid="IwAR0xyz"),
            None,
            None,
            user_agent=_TIKTOK_UA,
        )
        assert (source, medium) == ("facebook", DERIVED_MEDIUM)

    def test_user_agent_beats_referrer(self):
        # A TikTok bio link routed via linktr.ee: the webview is proof of
        # origin, the referrer is only the intermediary.
        source, _ = effective_utm_source_medium(
            None,
            None,
            None,
            referrer="https://linktr.ee/vionne",
            user_agent=_TIKTOK_UA,
        )
        assert source == "tiktok"

    def test_tiktok_organic_with_no_referrer_at_all(self):
        # The production case: TikTok's webview sends no referrer, so the
        # User-Agent is the only signal that exists.
        assert effective_utm_source_medium(
            None, None, None, referrer=None, user_agent=_TIKTOK_UA
        ) == ("tiktok", MEDIUM_SOCIAL)

    def test_instagram_organic_via_referrer_only(self):
        # 229 such visits in 30 days were being recorded as Direct.
        assert effective_utm_source_medium(
            None,
            None,
            None,
            referrer="https://l.instagram.com/",
            user_agent=_PLAIN_UA,
        ) == ("instagram", MEDIUM_SOCIAL)

    def test_falls_back_to_touch_referrer(self):
        touch = _touch(referrer="https://l.instagram.com/")
        assert effective_utm_source_medium(touch, None, None)[0] == "instagram"

    def test_caller_medium_is_never_overwritten(self):
        source, medium = effective_utm_source_medium(
            None, None, "cpc", user_agent=_TIKTOK_UA
        )
        assert (source, medium) == ("tiktok", "cpc")

    def test_direct_traffic_stays_direct(self):
        assert effective_utm_source_medium(
            None, None, None, referrer=None, user_agent=_PLAIN_UA
        ) == (None, None)
