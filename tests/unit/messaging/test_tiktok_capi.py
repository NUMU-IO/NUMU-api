"""Unit tests for the TikTok Events API Celery task helpers (pure pieces).

Covers the funnel-step→event map (TikTok renamed CompletePayment → Purchase
and SubmitForm → Lead on 2025-05-01), the Meta-shaped custom_data → TikTok
properties transform, response redaction, and backoff. The full async
_send_event (DB + httpx) is out of scope here (no live TikTok calls).
"""

from __future__ import annotations

from src.infrastructure.messaging.tasks.tiktok_capi import (
    FUNNEL_STEP_TO_TIKTOK_EVENT,
    _backoff_from_response,
    _funnel_step_to_tiktok_event,
    _redact_response,
    _to_tiktok_properties,
)


class TestFunnelMap:
    def test_uses_the_current_renamed_event_codes(self):
        """TikTok renamed these on 2025-05-01. The legacy names still work via
        auto-conversion, so this asserts intent, not survival: the storefront's
        map must say the same thing, because dedup keys on the event NAME."""
        assert FUNNEL_STEP_TO_TIKTOK_EVENT["order_completed"] == "Purchase"
        assert FUNNEL_STEP_TO_TIKTOK_EVENT["lead"] == "Lead"
        assert "CompletePayment" not in FUNNEL_STEP_TO_TIKTOK_EVENT.values()
        assert "SubmitForm" not in FUNNEL_STEP_TO_TIKTOK_EVENT.values()

    def test_purchase_event_names_covers_the_legacy_row_name(self):
        """Log rows written before the rename say CompletePayment; the sweep
        reads them back and would re-send every historical order without it."""
        from src.infrastructure.messaging.tasks.tiktok_capi import (
            PURCHASE_EVENT_NAMES,
        )

        assert set(PURCHASE_EVENT_NAMES) == {"Purchase", "CompletePayment"}
        assert FUNNEL_STEP_TO_TIKTOK_EVENT["order_completed"] in PURCHASE_EVENT_NAMES

    def test_core_commerce_events(self):
        assert FUNNEL_STEP_TO_TIKTOK_EVENT["product_view"] == "ViewContent"
        assert FUNNEL_STEP_TO_TIKTOK_EVENT["add_to_cart"] == "AddToCart"
        assert FUNNEL_STEP_TO_TIKTOK_EVENT["checkout_started"] == "InitiateCheckout"
        assert FUNNEL_STEP_TO_TIKTOK_EVENT["search"] == "Search"

    def test_page_view_absent_server_side(self):
        # Bare page views fire via ttq.page() in the browser, not the server.
        assert "page_view" not in FUNNEL_STEP_TO_TIKTOK_EVENT

    def test_helper_returns_none_for_unknown(self):
        assert _funnel_step_to_tiktok_event("nope") is None
        assert _funnel_step_to_tiktok_event("order_completed") == "Purchase"


class TestPropertiesTransform:
    def test_maps_meta_shape_to_tiktok(self):
        props = _to_tiktok_properties({
            "value": 59.98,
            "currency": "EGP",
            "content_ids": ["A", "B"],
            "contents": [{"id": "A", "quantity": 2, "item_price": 29.99}],
            "num_items": 2,
            "order_id": "ORD-1",
        })
        assert props["value"] == 59.98
        assert props["currency"] == "EGP"
        assert props["content_type"] == "product"
        assert props["content_id"] == "A,B"
        assert props["contents"][0] == {
            "content_id": "A",
            "quantity": 2,
            "price": 29.99,
        }
        assert props["quantity"] == 2
        assert props["order_id"] == "ORD-1"

    def test_currency_defaults_to_egp(self):
        assert _to_tiktok_properties({})["currency"] == "EGP"

    def test_contents_synthesized_from_content_ids(self):
        """ViewContent / AddToCart / the thank-you Purchase send only
        `content_ids`; TikTok's "Content ID is missing" diagnostic reads
        `contents[].content_id`, so the mapper must build the lines itself."""
        props = _to_tiktok_properties({
            "content_ids": ["6dc03192-f6a3-4100-b593-8cb185bc7bbe"],
            "content_name": "Degradee - Blue & Baby Blue",
            "content_type": "product",
            "value": 250,
            "currency": "EGP",
        })
        assert props["content_ids"] == ["6dc03192-f6a3-4100-b593-8cb185bc7bbe"]
        assert props["contents"] == [
            {
                "content_id": "6dc03192-f6a3-4100-b593-8cb185bc7bbe",
                "quantity": 1,
                "content_name": "Degradee - Blue & Baby Blue",
                "price": 250,
            }
        ]

    def test_multi_id_synthesis_never_invents_a_price(self):
        props = _to_tiktok_properties({"content_ids": ["A", "B"], "value": 500})
        assert props["contents"] == [
            {"content_id": "A", "quantity": 1},
            {"content_id": "B", "quantity": 1},
        ]

    def test_purchase_payload_never_passes_order_total_as_price(self):
        """A single-item order's `value` is the order total (shipping, tax,
        fees). It must not be reported as the product's price."""
        props = _to_tiktok_properties({
            "content_ids": ["A"],
            "num_items": 1,
            "value": 300,
            "order_id": "o1",
        })
        assert props["contents"] == [{"content_id": "A", "quantity": 1}]

    def test_blank_content_ids_are_dropped_everywhere(self):
        props = _to_tiktok_properties({
            "content_ids": ["A", "", "  "],
            "contents": [{"id": "", "quantity": 1}, {"id": "A", "quantity": 2}],
        })
        assert props["content_id"] == "A"
        assert props["contents"] == [{"content_id": "A", "quantity": 2, "price": 0}]

    def test_no_ids_at_all_means_no_contents_key(self):
        assert "contents" not in _to_tiktok_properties({"value": 1})

    def test_search_query_passthrough(self):
        assert _to_tiktok_properties({"query": "hijab"})["query"] == "hijab"


class TestRedactResponse:
    def test_none_returns_none(self):
        assert _redact_response(None) is None

    def test_keeps_only_diagnostic_keys(self):
        out = _redact_response({
            "code": 0,
            "message": "OK",
            "request_id": "R1",
            "data": {"secret": 1},
        })
        assert out == {"code": 0, "message": "OK", "request_id": "R1"}

    def test_preserves_raw_fallback(self):
        out = _redact_response({"raw": "not json", "code": 40001})
        assert out["raw"] == "not json"
        assert out["code"] == 40001


class TestBackoff:
    def test_retry_after_header_wins(self):
        headers = {"retry-after": "42"}
        assert _backoff_from_response(headers, retries=0) == 42

    def test_exponential_when_no_header(self):
        assert _backoff_from_response({}, retries=3) == 8

    def test_capped_at_300(self):
        assert _backoff_from_response({}, retries=20) == 300


class TestDedupKey:
    """The UNIQUE key must include pixel_id.

    Both fan-out paths (`/track` and the purchase dispatcher) enqueue one
    task per api-enabled pixel with the SAME event_id, because TikTok's own
    dedup window is scoped to a single Pixel Code. Keying the log on
    (store_id, event_id) alone made every task after the first return
    {"status": "duplicate"} before any HTTP call — a multi-pixel store was
    silently a single-pixel store.
    """

    def test_unique_constraint_is_store_pixel_event(self):
        from sqlalchemy import UniqueConstraint

        from src.infrastructure.database.models.tenant.tiktok_event_log import (
            TikTokEventLogModel,
        )

        uniques = [
            c
            for c in TikTokEventLogModel.__table__.constraints
            if isinstance(c, UniqueConstraint)
        ]
        assert len(uniques) == 1, "expected exactly one UNIQUE constraint"
        assert [c.name for c in uniques[0].columns] == [
            "store_id",
            "pixel_id",
            "event_id",
        ]


class TestAdoptionRule:
    """Which UNIQUE-violating row the worker is allowed to finish.

    Getting this wrong is invisible: too strict and an event is written,
    never sent, and looks identical in the log to one that was; too loose
    and a second producer re-sends an event TikTok already counted.
    """

    class _Row:
        def __init__(self, sent_at=None, response_status=None):
            self.sent_at = sent_at
            self.response_status = response_status

    def test_no_row_is_never_adopted(self):
        from src.infrastructure.messaging.tasks.tiktok_capi import (
            should_adopt_existing_row,
        )

        assert should_adopt_existing_row(None, 0) is False
        assert should_adopt_existing_row(None, 3) is False

    def test_a_retry_owns_its_own_row(self):
        from src.infrastructure.messaging.tasks.tiktok_capi import (
            should_adopt_existing_row,
        )

        answered = self._Row(sent_at="2026-09-08", response_status=500)
        assert should_adopt_existing_row(answered, retries=1) is True

    def test_unanswered_row_is_adopted_even_on_first_delivery(self):
        """The crash case: acks_late redelivers with retries == 0. Before
        this, the event stayed written-but-never-sent forever."""
        from src.infrastructure.messaging.tasks.tiktok_capi import (
            should_adopt_existing_row,
        )

        assert should_adopt_existing_row(self._Row(), retries=0) is True

    def test_answered_row_on_first_delivery_is_a_real_duplicate(self):
        from src.infrastructure.messaging.tasks.tiktok_capi import (
            should_adopt_existing_row,
        )

        delivered = self._Row(sent_at="2026-09-08", response_status=200)
        assert should_adopt_existing_row(delivered, retries=0) is False
        # A recorded FAILURE is still an answer — the retry machinery owns
        # that row, not a fresh producer.
        failed = self._Row(sent_at="2026-09-08", response_status=400)
        assert should_adopt_existing_row(failed, retries=0) is False
