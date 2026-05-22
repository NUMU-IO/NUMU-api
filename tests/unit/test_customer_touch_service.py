"""Unit tests for customer_touch_service pure-Python helpers.

The DB-write paths (maybe_capture_touch, backfill_session_touches) are
covered by integration tests. This file focuses on the two pure
helpers that decide whether to capture and whether to dedup:

* ``_has_attribution_signal`` — gate against internal-nav noise
* ``_utms_equal`` — dedup against the immediately-prior touch
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from src.application.services.customer_touch_service import (
    _has_attribution_signal,
    _utms_equal,
)
from src.infrastructure.database.models.tenant.customer_touch import (
    CustomerTouchModel,
)


def _row(**fields) -> CustomerTouchModel:
    """Lightweight ``CustomerTouchModel`` stand-in for dedup tests.

    The dedup function only reads utm_source / medium / campaign, so
    the rest of the fields don't matter for these tests.
    """
    return CustomerTouchModel(
        utm_source=fields.get("utm_source"),
        utm_medium=fields.get("utm_medium"),
        utm_campaign=fields.get("utm_campaign"),
        utm_term=fields.get("utm_term"),
        utm_content=fields.get("utm_content"),
        ts=fields.get("ts", datetime.now(UTC)),
        session_fingerprint=fields.get("session_fingerprint", "fp"),
    )


class TestHasAttributionSignal:
    def test_all_none_returns_false(self):
        # A track event with zero signal is internal nav — skip.
        assert not _has_attribution_signal(
            utm_source=None,
            utm_medium=None,
            utm_campaign=None,
            gclid=None,
            fbclid=None,
            referrer=None,
        )

    def test_utm_source_present(self):
        assert _has_attribution_signal(
            utm_source="facebook",
            utm_medium=None,
            utm_campaign=None,
            gclid=None,
            fbclid=None,
            referrer=None,
        )

    def test_only_referrer(self):
        # External referrer alone counts — that's a legitimate
        # acquisition touch (organic FB share, blog link, etc.).
        assert _has_attribution_signal(
            utm_source=None,
            utm_medium=None,
            utm_campaign=None,
            gclid=None,
            fbclid=None,
            referrer="https://news.example.com/article",
        )

    def test_only_gclid(self):
        # Google Ads autotag — utm_source is sometimes left empty
        # when gclid is doing the work.
        assert _has_attribution_signal(
            utm_source=None,
            utm_medium=None,
            utm_campaign=None,
            gclid="abc-123",
            fbclid=None,
            referrer=None,
        )

    def test_only_fbclid(self):
        assert _has_attribution_signal(
            utm_source=None,
            utm_medium=None,
            utm_campaign=None,
            gclid=None,
            fbclid="IwAR0xyz",
            referrer=None,
        )

    def test_empty_strings_treated_as_none(self):
        # The storefront sometimes sends "" instead of null for empty
        # query params; must not count as a touch.
        assert not _has_attribution_signal(
            utm_source="",
            utm_medium="",
            utm_campaign="",
            gclid="",
            fbclid="",
            referrer="",
        )

    def test_whitespace_only_treated_as_none(self):
        assert not _has_attribution_signal(
            utm_source="   ",
            utm_medium="",
            utm_campaign=None,
            gclid=None,
            fbclid=None,
            referrer="\t\n",
        )


class TestUtmsEqual:
    def test_identical_utms(self):
        row = _row(
            utm_source="facebook",
            utm_medium="social",
            utm_campaign="eid-sale-AB7K9X",
        )
        assert _utms_equal(
            row,
            utm_source="facebook",
            utm_medium="social",
            utm_campaign="eid-sale-AB7K9X",
        )

    def test_case_insensitive(self):
        # `Facebook` vs `facebook` is the same touch — refresh from a
        # share button vs typing the URL won't always preserve case.
        row = _row(utm_source="Facebook", utm_medium="Social")
        assert _utms_equal(
            row, utm_source="facebook", utm_medium="social", utm_campaign=None
        )

    def test_whitespace_insensitive(self):
        row = _row(utm_source="  facebook  ")
        assert _utms_equal(
            row, utm_source="facebook", utm_medium=None, utm_campaign=None
        )

    def test_different_source_not_equal(self):
        row = _row(utm_source="facebook", utm_medium="social")
        assert not _utms_equal(
            row, utm_source="instagram", utm_medium="social", utm_campaign=None
        )

    def test_different_campaign_not_equal(self):
        # Same source/medium but different campaign is a distinct touch.
        row = _row(utm_source="facebook", utm_campaign="eid-2026")
        assert not _utms_equal(
            row,
            utm_source="facebook",
            utm_medium=None,
            utm_campaign="ramadan-2026",
        )

    def test_ignores_utm_term(self):
        # term/content are creative IDs / search keywords, not touch
        # identity. Same source/medium/campaign with different term is
        # the SAME touch, not a new one.
        row = _row(
            utm_source="google",
            utm_medium="cpc",
            utm_campaign="brand",
            utm_term="abaya",
        )
        assert _utms_equal(
            row, utm_source="google", utm_medium="cpc", utm_campaign="brand"
        )

    def test_ignores_utm_content(self):
        row = _row(
            utm_source="facebook",
            utm_campaign="eid",
            utm_content="banner-v2",
        )
        assert _utms_equal(
            row, utm_source="facebook", utm_medium=None, utm_campaign="eid"
        )

    def test_all_none_equals_all_none(self):
        # Edge case: a row with no UTMs being compared to no UTMs.
        # The capture service would never get this far (because
        # _has_attribution_signal would skip), but the dedup math
        # should still be consistent.
        row = _row()
        assert _utms_equal(row, utm_source=None, utm_medium=None, utm_campaign=None)

    def test_none_vs_empty_treated_same(self):
        # None and "" should be equivalent for dedup — both mean
        # "this dimension is absent".
        row = _row(utm_source="", utm_medium=None)
        assert _utms_equal(row, utm_source=None, utm_medium="", utm_campaign=None)

    @pytest.mark.parametrize(
        "new_source,expected_equal",
        [
            ("facebook", True),  # same
            ("FACEBOOK", True),  # case-insensitive
            ("  facebook  ", True),  # whitespace
            ("facebook-ads", False),  # near-match but different
            ("", False),  # different from "facebook"
        ],
    )
    def test_source_normalization(self, new_source: str, expected_equal: bool):
        row = _row(utm_source="facebook")
        assert (
            _utms_equal(
                row,
                utm_source=new_source,
                utm_medium=None,
                utm_campaign=None,
            )
            == expected_equal
        )
