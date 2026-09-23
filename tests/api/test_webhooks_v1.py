"""What a merchant can promise their integrator about webhooks.

Each test here stands for a way the previous version broke that promise: an
event type that was accepted and never delivered, a secret that could not be
rotated, an endpoint that could not be corrected, and a signature that proved
who sent a payload but not when.

Pure logic; no Postgres, no network.
"""

import hashlib
import hmac
import json

import pytest
from pydantic import ValidationError

from src.api.v1.schemas.tenant.webhooks import (
    CreateWebhookSubscriptionRequest,
    UpdateWebhookSubscriptionRequest,
)
from src.application.services.webhook_delivery_service import WebhookDeliveryService
from src.application.use_cases.webhooks.create_subscription import VALID_EVENT_TYPES
from src.core.entities.webhook import SUBSCRIBABLE_EVENT_TYPES, WebhookEventType

SECRET = "s3cr3t"


def test_only_events_that_are_published_can_be_subscribed_to():
    """Thirteen names were accepted and never delivered. Never again."""
    assert VALID_EVENT_TYPES == {
        "order.created",
        "order.paid",
        "order.status_changed",
        "product.created",
        "product.updated",
        "product.deleted",
    }


def test_every_subscribable_event_has_a_publisher():
    """The catalogue and the dispatcher are one list, checked here."""
    import src.infrastructure.events.handlers.webhook_handler as handler

    source = open(handler.__file__, encoding="utf-8").read()
    for event in SUBSCRIBABLE_EVENT_TYPES:
        assert f"WebhookEventType.{event.name}" in source, event.value


def test_the_test_ping_is_deliverable_but_not_subscribable():
    """Subscribing to it would mean waiting for an event nobody publishes."""
    assert WebhookEventType.PING not in SUBSCRIBABLE_EVENT_TYPES
    assert "webhook.ping" not in VALID_EVENT_TYPES


def test_a_removed_event_type_is_refused_at_subscribe_time():
    request = CreateWebhookSubscriptionRequest(
        url="https://example.com/hook", events=["return.approved"]
    )
    assert "return.approved" not in VALID_EVENT_TYPES
    # The schema takes any string; the use case is what rejects it.
    assert request.events == ["return.approved"]


def test_an_update_may_change_one_field_alone():
    """Correcting a URL must not require re-stating the event list."""
    patch = UpdateWebhookSubscriptionRequest(is_active=True)

    assert patch.is_active is True
    assert patch.url is None and patch.events is None


def test_an_update_cannot_empty_the_event_list():
    with pytest.raises(ValidationError):
        UpdateWebhookSubscriptionRequest(events=[])


# ── Signing ─────────────────────────────────────────────────────────────


def test_the_original_signature_still_covers_the_body():
    """Existing receivers verify this header and must keep working."""
    body = json.dumps({"event": "order.paid"}).encode()

    signature = WebhookDeliveryService._sign(SECRET, body)

    expected = hmac.new(SECRET.encode(), body, hashlib.sha256).hexdigest()
    assert signature == f"sha256={expected}"


def test_the_v1_signature_covers_the_timestamp_too():
    """Body-only signatures stay valid forever, so a capture replays forever."""
    body = b'{"event":"order.paid"}'

    signed = WebhookDeliveryService._sign_v1(SECRET, body, 1_700_000_000)

    stamp, mac = signed.split(",")
    assert stamp == "t=1700000000"
    expected = hmac.new(
        SECRET.encode(), b"1700000000." + body, hashlib.sha256
    ).hexdigest()
    assert mac == f"v1={expected}"


def test_replaying_a_payload_under_a_new_timestamp_breaks_the_signature():
    body = b'{"event":"order.paid"}'

    original = WebhookDeliveryService._sign_v1(SECRET, body, 1_700_000_000)
    replayed = WebhookDeliveryService._sign_v1(SECRET, body, 1_700_000_900)

    assert original != replayed


def test_the_envelope_names_the_event_and_when_it_was_built():
    envelope = WebhookDeliveryService._build_envelope(
        WebhookEventType.ORDER_PAID, {"order_id": "abc"}
    )

    assert envelope["event"] == "order.paid"
    assert envelope["data"] == {"order_id": "abc"}
    assert envelope["timestamp"]


def test_a_delivery_names_its_store():
    """A Partner App gets every store's events at one URL; without the store
    in the payload it cannot tell whose order ``order.paid`` is."""
    from uuid import uuid4

    store_id = uuid4()
    envelope = WebhookDeliveryService._build_envelope(
        WebhookEventType.ORDER_PAID, {"order_id": "abc"}, store_id
    )

    assert envelope["data"] == {"store_id": str(store_id), "order_id": "abc"}
