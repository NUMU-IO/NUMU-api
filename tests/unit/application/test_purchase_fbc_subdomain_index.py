"""The Purchase-path ``fbc`` must agree with the one ``/track`` already sent.

Meta's ``fbc`` is ``fb.<subdomainIndex>.<creationTime>.<fbclid>``, and the
index counts the labels of the public suffix of the domain the cookie was set
on (``app`` → 1, ``com.eg`` → 2). ``/track`` derives it from the event's page
URL; the order-path Purchase rebuilt the same click id from the order's stored
attribution snapshot but passed no host at all, so it always emitted index 1.

On a ``*.com.eg`` custom domain — the ordinary shape of an Egyptian business
domain, and the reason ``click_id.py`` carries a suffix table in the first
place — that produced two different ``fbc`` values for one click, and the
malformed one was attached to the CONVERSION: the single event Meta optimises
ad spend against.
"""

from __future__ import annotations

from types import SimpleNamespace
from uuid import uuid4

from src.application.services.meta_capi_purchase_dispatcher import (
    _build_user_data_from_order,
    _store_host,
)

FBCLID = "IwAR0AbCdEf"
CLICK_TS = 1786838400  # epoch seconds


def _order(*, fbc_cookie: str | None = None, fbclid: str | None = FBCLID):
    meta = {}
    if fbc_cookie:
        meta["fbc"] = fbc_cookie
    return SimpleNamespace(
        id=uuid4(),
        store_id=uuid4(),
        customer_id=None,
        session_fingerprint="FP",
        shipping_address={"phone": "+201234567890", "country": "EG"},
        line_items=[],
        total=25_000,
        currency="EGP",
        metadata=meta,
        attribution={"last_touch": {"fbclid": fbclid, "ts": CLICK_TS}},
    )


class TestStoreHost:
    def test_strips_the_scheme_off_the_canonical_origin(self):
        assert _store_host(SimpleNamespace(store_url="https://vionneeg.com")) == (
            "vionneeg.com"
        )

    def test_custom_two_label_suffix_domain(self):
        assert _store_host(SimpleNamespace(store_url="https://shop.com.eg")) == (
            "shop.com.eg"
        )

    def test_no_store_url_is_none_not_an_error(self):
        assert _store_host(SimpleNamespace(store_url=None)) is None
        assert _store_host(SimpleNamespace()) is None


class TestPurchaseFbcIndex:
    def test_single_label_tld_gets_index_1(self):
        ud = _build_user_data_from_order(_order(), host="vionneeg.com")
        assert ud["fbc"] == f"fb.1.{CLICK_TS * 1000}.{FBCLID}"

    def test_multi_label_suffix_gets_index_2(self):
        """The bug: this used to come back as `fb.1.…` while /track sent
        `fb.2.…` for the very same click."""
        ud = _build_user_data_from_order(_order(), host="shop.com.eg")
        assert ud["fbc"] == f"fb.2.{CLICK_TS * 1000}.{FBCLID}"

    def test_platform_subdomain_gets_index_1(self):
        ud = _build_user_data_from_order(_order(), host="vionne.numueg.app")
        assert ud["fbc"] == f"fb.1.{CLICK_TS * 1000}.{FBCLID}"

    def test_omitted_host_still_defaults_rather_than_raising(self):
        assert _build_user_data_from_order(_order())["fbc"] == (
            f"fb.1.{CLICK_TS * 1000}.{FBCLID}"
        )


class TestFbcPrecedence:
    def test_the_real_cookie_always_wins_over_a_rebuild(self):
        """A `_fbc` the browser actually stored is ground truth; synthesis is
        only the fallback for when the Pixel never ran."""
        real = "fb.2.1700000000000.REALCOOKIECLICK"
        ud = _build_user_data_from_order(_order(fbc_cookie=real), host="shop.com.eg")
        assert ud["fbc"] == real

    def test_no_click_id_synthesizes_nothing(self):
        """A fabricated fbc claims an ad click Meta cannot join — worse than
        sending none."""
        ud = _build_user_data_from_order(_order(fbclid=None), host="shop.com.eg")
        assert ud["fbc"] is None
