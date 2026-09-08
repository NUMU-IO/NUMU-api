"""The provider registry must describe both rails without flattening them.

The value of this abstraction is that it names what Meta and TikTok do
differently. A test that only checked "both providers exist" would pass while
someone quietly made them identical — which would be a real bug, because the
two vendors genuinely disagree about phone hashing and payload shape.
"""

from __future__ import annotations

import hashlib

import pytest

from src.core.services.conversion_provider import (
    PROVIDER_KEYS,
    all_providers,
    get_provider,
)


def _sha(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


class TestRegistry:
    def test_both_rails_are_registered(self):
        assert set(PROVIDER_KEYS) == {"meta", "tiktok"}
        assert {p.key for p in all_providers()} == {"meta", "tiktok"}

    def test_unknown_key_raises_rather_than_defaulting(self):
        """Silently routing a shopper's hashed identity to the wrong ad
        platform is worse than a 500."""
        with pytest.raises(KeyError):
            get_provider("google")

    def test_providers_are_cached_not_rebuilt(self):
        assert get_provider("tiktok") is get_provider("tiktok")


class TestTheDifferencesSurvive:
    def test_phone_hashing_differs_by_vendor(self):
        """TikTok hashes E.164 WITH the leading '+', Meta without it. When
        these two ever agree, one of them has silently broken."""
        raw = {"phone": "01001234567"}
        tiktok = get_provider("tiktok").hash_user_data(raw)["phone"]
        meta = get_provider("meta").hash_user_data(raw)["ph"][0]

        assert tiktok == _sha("+201001234567")
        assert meta == _sha("201001234567")
        assert tiktok != meta

    def test_payload_shape_differs_by_vendor(self):
        custom_data = {"content_ids": ["a"], "value": 10.0, "currency": "EGP"}

        # Meta consumes custom_data as the funnel builds it.
        assert get_provider("meta").map_properties(custom_data) is custom_data

        # TikTok remaps it, and synthesizes contents[] from content_ids.
        props = get_provider("tiktok").map_properties(custom_data)
        assert props["contents"][0]["content_id"] == "a"
        assert props["content_id"] == "a"

    def test_event_names_agree_where_they_should(self):
        """Both vendors now use TikTok's post-2025 names for these, and a
        drift here is what doubles conversions."""
        for provider in all_providers():
            assert provider.event_name_for_step("order_completed") == "Purchase"
            assert provider.event_name_for_step("unknown_step") is None


class TestClassifierSignatureIsUniform:
    def test_delivered_is_none_on_both(self):
        for provider in all_providers():
            assert provider.classify_response(200, 0, {}) is None

    def test_meta_ignores_the_business_code(self):
        """Meta has no business-code dimension — the argument exists only so
        one signature covers both rails."""
        meta = get_provider("meta")
        assert meta.classify_response(500, None, {}) is not None
        assert meta.classify_response(500, 12345, {}) == meta.classify_response(
            500, None, {}
        )

    def test_tiktok_reads_the_business_code(self):
        """A 200 carrying a 5xxxx code is a server error, not a success —
        the whole reason TikTok needs the third argument."""
        tiktok = get_provider("tiktok")
        assert tiktok.classify_response(200, 50000, {}) is not None
