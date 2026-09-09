"""Tests for the shopper-safe view of a shipment (P10).

These endpoints are **public**, and one of them is keyed on a short,
guessable order number. So the tests that matter most are the ones
asserting what does *not* cross the boundary.

`Shipment.status_history` carries internal text — raw carrier errors,
merchant-written cancellation reasons, and the carrier's own status
strings. None of it may be exposed. This maps each entry to a status and
a timestamp, with copy written on our side.
"""

from datetime import UTC, datetime

import pytest

from src.application.services.shipment_public_view import (
    STATUS_COPY,
    public_events,
    public_shipment,
)
from src.core.entities.shipment import ShipmentStatus


class _Shipment:
    """Minimal stand-in — only the fields the view reads."""

    def __init__(self, history=None, status=ShipmentStatus.IN_TRANSIT, **kw):
        self.status_history = history or []
        self.status = status
        self.carrier = kw.get("carrier", "manual")
        self.tracking_number = kw.get("tracking_number", "NM7NA2ZQKG2C")
        self.delivered_at = kw.get("delivered_at")
        # Fields that must never surface.
        self.cod_amount = 65000
        self.metadata = {"notes": "customer asked to call first"}


def _entry(to: str, description: str = "", when: str | None = None) -> dict:
    return {
        "from": "created",
        "to": to,
        "description": description,
        "timestamp": when or datetime.now(UTC).isoformat(),
    }


class TestNothingInternalLeaks:
    """The whole reason this module exists."""

    def test_internal_descriptions_never_appear(self):
        """These are real formats from the codebase.

        `Auto-create failed: {error_msg}` embeds a raw carrier API error;
        `Cancelled: {reason}` embeds the merchant's own words.
        """
        leaky = [
            _entry("failed", "Auto-create failed: 401 invalid api_key sk_live_xyz"),
            _entry("cancelled", "Cancelled: customer was rude on the phone"),
            _entry("in_transit", "Synced from bosta API: IN_WAREHOUSE"),
        ]
        blob = repr(public_events(_Shipment(leaky)))
        for secret in ("sk_live_xyz", "rude on the phone", "IN_WAREHOUSE", "401"):
            assert secret not in blob, secret

    def test_the_shipment_view_carries_no_pii_or_money(self):
        blob = repr(public_shipment(_Shipment()))
        assert "65000" not in blob
        assert "call first" not in blob

    def test_labels_come_from_our_copy_not_the_history(self):
        events = public_events(
            _Shipment([_entry("delivered", "whatever the carrier said")])
        )
        assert events[0]["label_en"] == STATUS_COPY[ShipmentStatus.DELIVERED]["en"]
        assert events[0]["label_ar"] == STATUS_COPY[ShipmentStatus.DELIVERED]["ar"]


class TestJourney:
    def test_events_are_in_order_with_bilingual_labels(self):
        history = [
            _entry("created", when="2026-09-01T09:00:00+00:00"),
            _entry("picked_up", when="2026-09-01T14:00:00+00:00"),
            _entry("delivered", when="2026-09-02T11:00:00+00:00"),
        ]
        events = public_events(_Shipment(history))
        assert [e["status"] for e in events] == ["created", "picked_up", "delivered"]
        for e in events:
            assert e["label_en"] and e["label_ar"]
            assert isinstance(e["occurred_at"], datetime)

    def test_a_repeated_status_collapses(self):
        """A carrier re-sending "in transit" three times is one movement,
        not three."""
        history = [_entry("in_transit") for _ in range(3)]
        assert len(public_events(_Shipment(history))) == 1

    def test_unknown_statuses_are_dropped_not_guessed(self):
        history = [_entry("created"), _entry("teleported"), _entry("delivered")]
        assert [e["status"] for e in public_events(_Shipment(history))] == [
            "created",
            "delivered",
        ]

    def test_malformed_history_does_not_break_the_page(self):
        """History is free-form JSON on the entity."""
        history = ["nope", {}, None, {"to": None}, _entry("delivered")]
        assert [e["status"] for e in public_events(_Shipment(history))] == ["delivered"]

    def test_missing_timestamp_is_null_not_invented(self):
        events = public_events(_Shipment([{"to": "delivered"}]))
        assert events[0]["occurred_at"] is None

    def test_no_history_is_an_empty_journey_not_an_error(self):
        assert public_events(_Shipment([])) == []
        assert public_shipment(_Shipment([]))["events"] == []


class TestCopy:
    def test_every_status_has_bilingual_copy(self):
        """A status with no copy would render blank to a customer."""
        for status in ShipmentStatus:
            assert status in STATUS_COPY, status
            assert STATUS_COPY[status]["en"]
            assert STATUS_COPY[status]["ar"]

    def test_arabic_copy_is_actually_arabic(self):
        for status, copy in STATUS_COPY.items():
            assert any("؀" <= ch <= "ۿ" for ch in copy["ar"]), status

    def test_failed_does_not_tell_a_customer_their_order_is_lost(self):
        """The courier normally tries again; "failed" reads as final."""
        copy = STATUS_COPY[ShipmentStatus.FAILED]
        assert "failed" not in copy["en"].lower().replace("didn't succeed", "")
        assert "attempt" in copy["en"].lower()


class TestManualCarrier:
    """The gap P2 created: a NUMU number with nowhere to go."""

    def test_manual_shipment_has_no_external_tracking_url(self):
        from src.application.services.carrier_resolver import tracking_url_for

        assert tracking_url_for("manual", "NM1") is None

    def test_the_journey_is_the_tracking_for_a_manual_parcel(self):
        """With no carrier site, these events are all the customer gets —
        so they have to be there."""
        history = [_entry("created"), _entry("picked_up"), _entry("out_for_delivery")]
        view = public_shipment(_Shipment(history, carrier="manual"), tracking_url=None)
        assert view["tracking_url"] is None
        assert view["tracking_number"] == "NM7NA2ZQKG2C"
        assert len(view["events"]) == 3

    @pytest.mark.parametrize("carrier", ["bosta", "mylerz", "jt"])
    def test_a_carrier_with_a_site_still_gets_its_link(self, carrier):
        from src.application.services.carrier_resolver import tracking_url_for

        view = public_shipment(
            _Shipment(carrier=carrier),
            tracking_url=tracking_url_for(carrier, "ABC123"),
        )
        assert view["tracking_url"] and "ABC123" in view["tracking_url"]


class TestEndpointWiring:
    def test_both_tracking_endpoints_pass_the_shipment_repo(self):
        """They share a builder so they cannot drift into showing
        different field sets — that only holds if both pass it."""
        import inspect

        from src.api.v1.routes.storefront import order_tracking as mod

        source = inspect.getsource(mod)
        assert (
            source.count(
                "_build_tracking_response(order, store, product_repo, shipment_repo)"
            )
            == 2
        )

    def test_a_shipment_lookup_failure_does_not_break_tracking(self):
        """A customer chasing a parcel must not meet an error page."""
        import inspect

        from src.api.v1.routes.storefront import order_tracking as mod

        source = inspect.getsource(mod._build_tracking_response)
        assert "except Exception" in source
        assert "tracking_shipment_lookup_failed" in source
