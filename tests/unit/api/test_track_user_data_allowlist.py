"""Unit tests for the ``/track`` ``user_data`` allowlist and host resolution.

``/track`` is unauthenticated and same-origin with BYOT theme bundles, so
``body.user_data`` is attacker-controlled in the practical sense: any script
a merchant installs can POST it. The allowlist is a security control, not a
tidiness measure — before it existed, a page script could set
``user_data.ip`` / ``user_data.user_agent`` and replace the two match keys
that carry ALL anonymous traffic. A real value that is uniformly wrong is
worse than no value: it silently degrades match quality for every shopper
while every diagnostic reports the parameter as covered.

Sibling of ``test_track_custom_data_allowlist.py`` (api#466), which does the
same job for ``custom_data``.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from src.api.v1.routes.storefront.tracking import (
    _CLIENT_USER_DATA_ALLOWLIST,
    _CLIENT_USER_DATA_MAX_LEN,
    _client_supplied_user_data,
    _request_host,
    _synthesize_fbc,
)


class _Body:
    """Duck-typed stand-in for ``TrackPageViewRequest``."""

    def __init__(self, page_url=None):
        self.page_url = page_url


# ---------------------------------------------------------------------------
# _client_supplied_user_data
# ---------------------------------------------------------------------------


class TestClientUserDataAllowlist:
    def test_allowed_pii_keys_pass_through(self):
        raw = {
            "email": "shopper@example.org",
            "phone": "01001234567",
            "first_name": "Sara",
            "last_name": "Ali",
            "city": "New Cairo",
            "state": "Cairo",
            "zip": "11511",
            "country_code": "EG",
        }
        assert _client_supplied_user_data(raw) == raw

    def test_allowlist_matches_what_the_storefront_actually_sends(self):
        """`identityForCapi()` in numu-storefront/src/lib/meta-identity.ts maps
        its 8 fields onto exactly these names. A key dropped here is identity
        the shopper typed and Meta never receives."""
        assert _CLIENT_USER_DATA_ALLOWLIST == {
            "email",
            "phone",
            "first_name",
            "last_name",
            "city",
            "state",
            "zip",
            "country_code",
        }

    @pytest.mark.parametrize(
        "key",
        ["ip", "user_agent", "fbp", "fbc", "external_id", "customer_id"],
    )
    def test_server_derived_keys_are_never_accepted_from_the_client(self, key: str):
        assert _client_supplied_user_data({key: "attacker-controlled"}) == {}

    def test_unknown_keys_are_dropped_silently(self):
        assert (
            _client_supplied_user_data({"em": "x", "__proto__": "y", "note": "z"}) == {}
        )

    def test_values_are_coerced_to_trimmed_strings(self):
        out = _client_supplied_user_data({"zip": 11511, "city": "  Cairo  "})
        assert out == {"zip": "11511", "city": "Cairo"}

    @pytest.mark.parametrize("value", [None, True, False, {"a": 1}, ["a"], "", "   "])
    def test_non_scalar_and_blank_values_are_dropped(self, value):
        # `bool` must be excluded explicitly — it is a subclass of int and
        # would otherwise stringify to "True".
        assert _client_supplied_user_data({"city": value}) == {}

    def test_oversized_values_are_dropped_not_truncated(self):
        # Truncating would produce a plausible-looking hash that can never
        # match; dropping is the honest failure.
        assert (
            _client_supplied_user_data({"city": "x" * (_CLIENT_USER_DATA_MAX_LEN + 1)})
            == {}
        )

    def test_value_at_the_length_boundary_is_kept(self):
        value = "x" * _CLIENT_USER_DATA_MAX_LEN
        assert _client_supplied_user_data({"city": value}) == {"city": value}

    @pytest.mark.parametrize("raw", [None, [], "string", 42, set()])
    def test_non_dict_payloads_return_empty(self, raw):
        assert _client_supplied_user_data(raw) == {}

    def test_arabic_values_survive_intact(self):
        out = _client_supplied_user_data({"city": "القاهرة", "first_name": "محمد"})
        assert out == {"city": "القاهرة", "first_name": "محمد"}

    def test_a_hostile_payload_cannot_poison_the_hashed_output(self):
        """End-to-end: run the projection through the real hasher and prove no
        client-controlled ip/ua/fbp/fbc reaches Meta's wire shape."""
        from src.infrastructure.external_services.meta.hashing import hash_user_data

        hostile = {
            "ip": "8.8.8.8",
            "user_agent": "curl/8",
            "fbp": "fb.1.0.attacker",
            "fbc": "fb.1.0.attacker",
            "external_id": "someone-elses-session",
            "email": "real@example.org",
        }
        hashed = hash_user_data(_client_supplied_user_data(hostile))
        assert hashed["client_ip_address"] is None
        assert hashed["client_user_agent"] is None
        assert hashed["fbp"] is None
        assert hashed["fbc"] is None
        assert hashed["external_id"] is None
        assert hashed["em"] is not None


# ---------------------------------------------------------------------------
# _request_host — feeds Meta's fbc subdomain index
# ---------------------------------------------------------------------------


class TestRequestHost:
    @pytest.mark.parametrize(
        ("page_url", "expected"),
        [
            ("https://vionne.numueg.app/products/x", "vionne.numueg.app"),
            ("https://Vionne.NumuEG.app/", "vionne.numueg.app"),  # normalized
            ("https://shop.vionne.com.eg:3100/cart", "shop.vionne.com.eg"),
            ("http://localhost:3100/", "localhost"),
        ],
    )
    def test_hostname_extracted(self, page_url: str, expected: str):
        assert _request_host(_Body(page_url)) == expected

    @pytest.mark.parametrize("page_url", [None, "", "not a url", "/relative/path"])
    def test_unusable_urls_return_none(self, page_url):
        # None makes the click-id builder fall back to its default index.
        assert _request_host(_Body(page_url)) is None

    def test_missing_attribute_is_survivable(self):
        assert _request_host(object()) is None


# ---------------------------------------------------------------------------
# _synthesize_fbc delegation — the name is referenced by
# docs/external-contracts.md #7, so its signature is a contract.
# ---------------------------------------------------------------------------


class TestSynthesizeFbcDelegate:
    def test_delegates_with_click_time_and_host(self):
        assert (
            _synthesize_fbc(
                "CID",
                datetime(2026, 8, 1, tzinfo=UTC),
                host="shop.vionne.com.eg",
            )
            == f"fb.2.{int(datetime(2026, 8, 1, tzinfo=UTC).timestamp() * 1000)}.CID"
        )

    def test_no_click_id_is_none(self):
        assert _synthesize_fbc(None, datetime.now(UTC)) is None
