"""Unit tests for ``meta/click_id.py`` — the single ``fbc`` builder.

The module shipped with zero direct coverage; the only exercise it got was
one happy-path assertion through the order dispatcher. ``fbc`` is the
strongest non-PII match key Meta has after hashed user data, and every one
of its three components has its own failure mode:

  * ``subdomainIndex`` wrong  → the value does not join to the browser cookie
  * ``creationTime`` wrong    → the click falls outside the attribution window
  * ``fbclid`` modified       → Meta cannot resolve the click at all
    (their spec says the id is case sensitive: "do not apply any
    modifications before using")

Test design follows the standard order: happy path → equivalence partitions
→ boundaries → error/degenerate inputs.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from src.infrastructure.external_services.meta.click_id import (
    subdomain_index_for_host,
    synthesize_fbc,
)

# ---------------------------------------------------------------------------
# subdomain_index_for_host — partitions: single-label TLD / multi-label
# suffix / degenerate input
# ---------------------------------------------------------------------------


class TestSubdomainIndex:
    @pytest.mark.parametrize(
        "host",
        [
            "vionne.numueg.app",  # the production shape
            "numueg.app",
            "www.rabbit.numueg.app",  # deeper subdomain, same suffix
            "shop.example.com",
            "brand.store",
            "brand.shop",
            "localhost",  # no dot at all
        ],
    )
    def test_single_label_suffix_is_index_1(self, host: str):
        assert subdomain_index_for_host(host) == 1

    @pytest.mark.parametrize(
        "host",
        [
            "vionne.com.eg",  # the normal Egyptian business domain
            "shop.vionne.com.eg",
            "store.com.sa",
            "brand.co.uk",
            "x.com.ae",
        ],
    )
    def test_multi_label_suffix_is_index_2(self, host: str):
        assert subdomain_index_for_host(host) == 2

    def test_case_and_port_and_trailing_dot_are_normalized(self):
        # A Host header legitimately carries a port; a FQDN legitimately
        # carries a trailing dot. Neither may change the index.
        assert subdomain_index_for_host("Vionne.COM.EG:3100") == 2
        assert subdomain_index_for_host("vionne.com.eg.") == 2
        assert subdomain_index_for_host("VIONNE.NUMUEG.APP") == 1

    @pytest.mark.parametrize("host", [None, "", "   ", ".", "..", ":8080"])
    def test_degenerate_hosts_fall_back_to_1(self, host):
        # 1 is correct for every single-label TLD, so the default is the
        # safe direction to fail in.
        assert subdomain_index_for_host(host) == 1

    def test_bare_multi_label_suffix_is_not_special_cased_below_two_labels(self):
        # "com.eg" itself is a public suffix, not a registrable domain. It is
        # not a host we can ever be served on, but it must not crash.
        assert subdomain_index_for_host("com.eg") == 2


# ---------------------------------------------------------------------------
# synthesize_fbc
# ---------------------------------------------------------------------------


class TestSynthesizeFbc:
    def test_happy_path_format(self):
        assert (
            synthesize_fbc("AbC_Click123", 1786838400)
            == "fb.1.1786838400000.AbC_Click123"
        )

    def test_host_drives_the_subdomain_index(self):
        assert synthesize_fbc("CID", 1786838400, host="vionne.com.eg").startswith(
            "fb.2."
        )
        assert synthesize_fbc("CID", 1786838400, host="vionne.numueg.app").startswith(
            "fb.1."
        )

    def test_fbclid_is_never_case_modified_or_trimmed_internally(self):
        # Meta: "do not apply any modifications before using". Mixed case plus
        # the base64url alphabet (- and _) must survive byte-for-byte.
        cid = "IwAR3xY-_Zq09AbCdEf"
        out = synthesize_fbc(cid, 1786838400)
        assert out.endswith("." + cid)
        assert out.split(".", 3)[3] == cid

    def test_datetime_input_is_converted_to_epoch_millis(self):
        ts = datetime(2026, 8, 17, 12, 0, 0, tzinfo=UTC)
        assert synthesize_fbc("CID", ts) == f"fb.1.{int(ts.timestamp() * 1000)}.CID"

    def test_epoch_millis_input_is_passed_through(self):
        # The attribution envelope has carried both units; 1e11 is the
        # documented seconds/millis boundary.
        assert synthesize_fbc("CID", 1786838400123) == "fb.1.1786838400123.CID"

    @pytest.mark.parametrize(
        ("value", "expected_ms"),
        [
            (99_999_999_999, 99_999_999_999_000),  # just below 1e11 → seconds
            (100_000_000_000, 100_000_000_000),  # at 1e11 → already millis
        ],
    )
    def test_seconds_vs_millis_boundary(self, value, expected_ms):
        assert synthesize_fbc("CID", value) == f"fb.1.{expected_ms}.CID"

    @pytest.mark.parametrize("fbclid", [None, ""])
    def test_no_click_id_returns_none(self, fbclid):
        assert synthesize_fbc(fbclid, 1786838400) is None

    @pytest.mark.parametrize("ts", [None, 0, -1, "not-a-number", object()])
    def test_unusable_timestamp_returns_none_rather_than_fabricating(self, ts):
        # A fabricated fbc is worse than none: it claims an ad click Meta
        # cannot join, and it does so on the conversion event.
        assert synthesize_fbc("CID", ts) is None

    def test_float_seconds_are_accepted(self):
        assert synthesize_fbc("CID", 1786838400.5) == "fb.1.1786838400500.CID"
