"""Contract test: pin the exact JSON NUMU POSTs to TikTok's Events API v1.3.

The mapper (`_to_tiktok_properties`) and the hashing module are the two
highest-churn files in the integration — both changed twice in the week of
2026-09-07 (contents[] synthesis, then the order-total price guard). Neither
had a test that asserted the *whole* body, so a regression could keep every
unit test green while changing what TikTok actually receives.

This asserts the full envelope for one realistic Egyptian COD purchase,
including the exact SHA-256 digests. If a digest here changes, browser↔server
deduplication and every match key changed with it — that is the point of
pinning them rather than asserting `"phone" in user`.

Deliberately NOT covered here: the Celery task, the DB and the HTTP call.
This is the shape contract only.
"""

from __future__ import annotations

import hashlib

from src.infrastructure.external_services.tiktok.hashing import hash_tiktok_user_data
from src.infrastructure.messaging.tasks.tiktok_capi import (
    _to_tiktok_properties,
    build_capi_payload,
)


def _sha(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


# One order: 2 × 250 EGP scarf + 1 × 150 EGP, EGP 650 total, guest COD buyer
# who arrived from a TikTok ad.
RAW_USER = {
    "email": "  Nour@Example.COM ",
    "phone": "01001234567",
    "first_name": "Nour",
    "last_name": "Hassan",
    "city": "New Cairo",
    "zip": "11835-2",
    "country_code": "EG",
    "customer_id": None,
    "external_id": "sess-abc123",
    "ip": "197.32.10.4",
    "user_agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 17_5 like Mac OS X)",
    "ttclid": "E.C.P.1234",
    "ttp": "ttp-cookie-value",
}

CUSTOM_DATA = {
    "value": 650.0,
    "currency": "EGP",
    "content_type": "product",
    "content_ids": ["uuid-a", "uuid-b"],
    "contents": [
        {"id": "uuid-a", "quantity": 2, "item_price": 250.0},
        {"id": "uuid-b", "quantity": 1, "item_price": 150.0},
    ],
    "num_items": 3,
    "order_id": "8f2a9c14-0000-4000-8000-000000000001",
}


class TestOutboundContract:
    def test_purchase_body_is_exactly_this(self):
        payload = build_capi_payload(
            pixel_id="D9GH5NRC77U5KEVKREF0",
            event_name="Purchase",
            event_time=1757280000,
            event_id="8f2a9c14-0000-4000-8000-000000000001",
            hashed_user=hash_tiktok_user_data(RAW_USER),
            properties=_to_tiktok_properties(CUSTOM_DATA),
            event_source_url="https://vionneeg.com/checkout/8f2a9c14/thank-you",
        )

        assert payload == {
            "event_source": "web",
            "event_source_id": "D9GH5NRC77U5KEVKREF0",
            "data": [
                {
                    "event": "Purchase",
                    "event_time": 1757280000,
                    "event_id": "8f2a9c14-0000-4000-8000-000000000001",
                    "user": {
                        # lowercased + trimmed
                        "email": _sha("nour@example.com"),
                        # E.164 WITH the leading "+" — TikTok's rule, not Meta's
                        "phone": _sha("+201001234567"),
                        # guest: session fingerprint, not customer_id
                        "external_id": _sha("sess-abc123"),
                        "first_name": _sha("nour"),
                        "last_name": _sha("hassan"),
                        # spaces stripped, so it matches Meta's `ct` byte for byte
                        "city": _sha("newcairo"),
                        # dashes removed
                        "zip_code": _sha("118352"),
                        # ISO-3166-1 alpha-2, lowercase
                        "country": _sha("eg"),
                        # raw, never hashed
                        "ttclid": "E.C.P.1234",
                        "ttp": "ttp-cookie-value",
                        "ip": "197.32.10.4",
                        "user_agent": (
                            "Mozilla/5.0 (iPhone; CPU iPhone OS 17_5 like Mac OS X)"
                        ),
                    },
                    "properties": {
                        "value": 650.0,
                        "currency": "EGP",
                        "content_type": "product",
                        "content_id": "uuid-a,uuid-b",
                        "content_ids": ["uuid-a", "uuid-b"],
                        "contents": [
                            {"content_id": "uuid-a", "quantity": 2, "price": 250.0},
                            {"content_id": "uuid-b", "quantity": 1, "price": 150.0},
                        ],
                        "quantity": 3,
                        "order_id": "8f2a9c14-0000-4000-8000-000000000001",
                    },
                    "page": {"url": "https://vionneeg.com/checkout/8f2a9c14/thank-you"},
                }
            ],
        }

    def test_test_event_code_sits_at_the_envelope_root(self):
        payload = build_capi_payload(
            pixel_id="PIX",
            event_name="ViewContent",
            event_time=1757280000,
            event_id="e1",
            hashed_user={},
            properties={},
            test_event_code="TT-1234",
        )
        assert payload["test_event_code"] == "TT-1234"
        assert "test_event_code" not in payload["data"][0]

    def test_opt_out_sets_limited_data_use_on_the_event(self):
        payload = build_capi_payload(
            pixel_id="PIX",
            event_name="ViewContent",
            event_time=1757280000,
            event_id="e1",
            hashed_user={},
            properties={},
            opt_out=True,
        )
        assert payload["data"][0]["limited_data_use"] is True

    def test_absent_fields_are_omitted_not_null(self):
        """A digest of an empty string matches nothing and is worse than
        sending no field at all, so empties must be dropped entirely."""
        user = hash_tiktok_user_data({"email": "", "phone": None, "city": "  "})
        assert user == {}

        payload = build_capi_payload(
            pixel_id="PIX",
            event_name="ViewContent",
            event_time=1757280000,
            event_id="e1",
            hashed_user=user,
            properties={},
        )
        assert "page" not in payload["data"][0]
        assert "limited_data_use" not in payload["data"][0]
        assert "test_event_code" not in payload


class TestCountryCanonicalization:
    """Country is canonicalized to ISO-3166-1 alpha-2 *before* hashing, and
    only a genuinely unmappable value is dropped — `sha256("Egypt")` would
    match nothing, so a field that can never match is worse than an absent
    one."""

    def test_name_and_code_hash_identically(self):
        for value in ("Egypt", "eg", "EG"):
            assert hash_tiktok_user_data({"country_code": value})["country"] == _sha(
                "eg"
            )

    def test_unmappable_value_is_dropped(self):
        assert "country" not in hash_tiktok_user_data({"country_code": "Narnia"})


class TestSearchTerm:
    """The storefront funnel builds Meta-shaped custom_data, so a Search
    arrives as `search_string`. TikTok's Events API reference names `query`
    and its Pixel standard-events table names `search_string`, so both go
    out — otherwise TikTok received a Search event with no term at all."""

    def test_meta_shaped_search_string_reaches_tiktok(self):
        props = _to_tiktok_properties({"search_string": "hijab", "currency": "EGP"})
        assert props["query"] == "hijab"
        assert props["search_string"] == "hijab"

    def test_native_query_key_still_works(self):
        props = _to_tiktok_properties({"query": "scarf"})
        assert props["query"] == "scarf"
        assert props["search_string"] == "scarf"

    def test_no_term_emits_neither_key(self):
        props = _to_tiktok_properties({"value": 1})
        assert "query" not in props
        assert "search_string" not in props


class TestReplayRebuildsTheSameWire:
    """Replay re-sends the STORED payload, it does not rebuild it.

    The stored row already holds the hashed `user` and the mapped
    `properties`. Re-deriving them on replay would re-hash already-hashed
    values and could drift from what the browser leg sent, which is what
    dedup depends on. `event_id` and `event_time` are carried through
    unchanged, so TikTok merges the replay into the original event instead
    of counting a second conversion.
    """

    def test_stored_payload_reproduces_the_original_body(self):
        hashed_user = hash_tiktok_user_data(RAW_USER)
        properties = _to_tiktok_properties(CUSTOM_DATA)
        original = build_capi_payload(
            pixel_id="PIX",
            event_name="Purchase",
            event_time=1757280000,
            event_id="order-1",
            hashed_user=hashed_user,
            properties=properties,
            event_source_url="https://vionneeg.com/t",
        )

        # Exactly what `_send_event` writes to `tiktok_event_log`.
        stored = {
            "event": "Purchase",
            "event_time": 1757280000,
            "event_source_url": "https://vionneeg.com/t",
            "action_source": "web",
            "properties": properties,
            "user": hashed_user,
            "test_event_code": None,
        }

        replayed = build_capi_payload(
            pixel_id="PIX",
            event_name=stored["event"],
            event_time=stored["event_time"],
            event_id="order-1",
            hashed_user=stored["user"],
            properties=stored["properties"],
            event_source_url=stored["event_source_url"],
            test_event_code=stored["test_event_code"],
            opt_out=bool(stored.get("limited_data_use")),
        )

        assert replayed == original
        assert replayed["data"][0]["user"]["phone"] == _sha("+201001234567"), (
            "a replay must never re-hash an already-hashed digest"
        )

    def test_opt_out_survives_a_replay(self):
        stored = {
            "event": "ViewContent",
            "event_time": 1757280000,
            "properties": {},
            "user": {},
            "limited_data_use": True,
        }
        replayed = build_capi_payload(
            pixel_id="PIX",
            event_name=stored["event"],
            event_time=stored["event_time"],
            event_id="e1",
            hashed_user=stored["user"],
            properties=stored["properties"],
            opt_out=bool(stored.get("limited_data_use")),
        )
        assert replayed["data"][0]["limited_data_use"] is True
