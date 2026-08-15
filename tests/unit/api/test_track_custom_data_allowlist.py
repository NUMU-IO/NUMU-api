"""Unit tests for the ``custom_data`` allowlist on the public /track endpoint.

``step_data`` is browser-supplied on an unauthenticated route, and under V3
BYOT the page is running a third-party theme bundle — so whatever that bundle
hands ``useAnalytics().track()`` reaches this dict. Event *names* were already
constrained to a fixed map; these tests pin the same guarantee for parameters.

The allowlist is a UNION of both vendors: Meta reads ``search_string`` and
forwards ``custom_data`` wholesale to Graph, while TikTok's ``toTikTokProps``
reads ``query`` / ``num_items`` / ``order_id`` off the same dict. Dropping
either vendor's keys would silently break that vendor's events.
"""

from __future__ import annotations

from src.api.v1.routes.storefront.tracking import (
    _ALLOWED_CUSTOM_DATA_KEYS,
    sanitize_custom_data,
)


class TestStandardParametersSurvive:
    def test_meta_purchase_payload_passes_through_unchanged(self):
        payload = {
            "value": 250.0,
            "currency": "EGP",
            "content_ids": ["04467124-7ff0-41c7-bef3-b1b8d7a99da4"],
            "content_type": "product",
            "contents": [{"id": "abc", "quantity": 2, "item_price": 125.0}],
            "num_items": 2,
            "order_id": "ord_123",
        }
        assert sanitize_custom_data(payload) == payload

    def test_meta_search_string_survives(self):
        assert sanitize_custom_data({"search_string": "linen scarf"}) == {
            "search_string": "linen scarf"
        }

    def test_tiktok_query_survives(self):
        # TikTok's search term has a different name than Meta's. Regression
        # guard: dropping this makes TikTok Search events lose their term.
        assert sanitize_custom_data({"query": "linen scarf"}) == {
            "query": "linen scarf"
        }

    def test_content_name_and_category_survive(self):
        payload = {"content_name": "Sponge - Army Green", "content_category": "Scarves"}
        assert sanitize_custom_data(payload) == payload


class TestUnknownKeysAreDropped:
    def test_arbitrary_theme_key_is_dropped(self):
        out = sanitize_custom_data({"value": 10, "shopper_note": "anything at all"})
        assert out == {"value": 10}

    def test_sensitive_looking_keys_never_forwarded(self):
        # The point of the allowlist: a third-party bundle cannot put attributes
        # about the shopper into a vendor payload, whatever it calls them.
        out = sanitize_custom_data({
            "currency": "EGP",
            "religion": "…",
            "user_religion": "…",
            "inferred_audience": "…",
            "email": "shopper@example.com",
            "phone": "+201000000000",
        })
        assert out == {"currency": "EGP"}

    def test_navigation_noise_is_dropped(self):
        # page_view posts {path, referrer}; neither is a vendor parameter.
        assert (
            sanitize_custom_data({"path": "/cart", "referrer": "https://x.test"}) == {}
        )

    def test_drops_keys_but_keeps_the_event(self):
        # An unknown field costs that field, never the conversion.
        assert sanitize_custom_data({"value": 99.0, "junk": 1}) == {"value": 99.0}


class TestEdgeCases:
    def test_none_yields_empty_dict(self):
        assert sanitize_custom_data(None) == {}

    def test_empty_yields_empty_dict(self):
        assert sanitize_custom_data({}) == {}

    def test_falsy_values_are_preserved_not_stripped(self):
        # 0.0 is a legitimate value (free item / 100%-discounted order); an
        # allowlist must filter by KEY, never by truthiness.
        assert sanitize_custom_data({"value": 0.0, "num_items": 0}) == {
            "value": 0.0,
            "num_items": 0,
        }

    def test_does_not_mutate_the_input(self):
        payload = {"value": 5, "junk": "x"}
        sanitize_custom_data(payload)
        assert payload == {"value": 5, "junk": "x"}

    def test_allowlist_covers_both_vendors_search_params(self):
        assert {"search_string", "query"} <= _ALLOWED_CUSTOM_DATA_KEYS
