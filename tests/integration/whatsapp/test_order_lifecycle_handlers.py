"""Integration tests for US1 — order_created + order_paid WhatsApp handlers.

Covers acceptance scenarios from spec.md (US1) and FRs 001-005:

- AS-1 / FR-001 — OrderCreatedEvent → order_confirmation message sent
- AS-2 / FR-002 — OrderPaidEvent → payment_received message sent
- AS-3        — opt-out customer → no send, reason=opt_out
- AS-4        — merchant order_confirmation toggle off → no send,
                reason=merchant_setting_off
- AS-5 / FR-005 — duplicate event → only one send (idempotent)

These tests use real SQLAlchemy session (RLS-aware) + a mocked
``WhatsAppMessagingService.send_*`` so we can assert dispatch parameters
without burning Meta API quota or requiring network.

NUMU_RUN_INTEGRATION_TESTS=1 to execute (matches the existing
notification-integration-test convention in this repo).
"""

from __future__ import annotations

import os
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest

from src.core.events.order_events import OrderCreatedEvent, OrderPaidEvent
from src.core.interfaces.services.messaging_service import (
    MessageChannel,
    MessageResult,
    MessageStatus,
)
from src.infrastructure.events.handlers.whatsapp_notification_handler import (
    handle_order_created_whatsapp,
    handle_order_paid_whatsapp,
)

pytestmark = pytest.mark.skipif(
    os.environ.get("NUMU_RUN_INTEGRATION_TESTS", "0") != "1",
    reason=("DB-backed integration tests; set NUMU_RUN_INTEGRATION_TESTS=1 to run."),
)


# ── Test fixture helpers ─────────────────────────────────────────────


@pytest.fixture
def mock_messaging_service():
    """A fake WhatsAppMessagingService with happy-path send_* returns.

    Patched in via ``get_whatsapp_service`` so the handler picks it up
    instead of touching the real Meta API.
    """
    service = AsyncMock()
    service.send_order_confirmation = AsyncMock(
        return_value=MessageResult(
            success=True,
            message_id="wamid.fake_oc_123",
            channel=MessageChannel.WHATSAPP,
            status=MessageStatus.SENT,
        )
    )
    service.send_payment_received = AsyncMock(
        return_value=MessageResult(
            success=True,
            message_id="wamid.fake_pr_456",
            channel=MessageChannel.WHATSAPP,
            status=MessageStatus.SENT,
        )
    )
    service._is_own = False
    return service


@pytest.fixture
def order_created_event_fixture(seeded_store, seeded_customer_optin_active):
    """An OrderCreatedEvent for a seeded store + opted-in customer.

    Relies on conftest-level fixtures (or per-test setup) that:
    - Insert a StoreModel with whatsapp_notifications.order_confirmation = True
    - Insert a CustomerModel with phone (E.164) + notification_prefs allowing
    - Insert a WhatsAppOptInModel for (store, phone), opted_out_at = NULL
    - Insert a system whatsapp_templates row (name='order_confirmation',
      language='ar', status='APPROVED')
    """
    return OrderCreatedEvent(
        order_id=uuid4(),
        order_number="A-0042",
        store_id=seeded_store.id,
        customer_id=seeded_customer_optin_active.id,
        total=250.00,
        currency="EGP",
    )


# ── AS-1 / FR-001 ────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_order_created_dispatches_order_confirmation(
    order_created_event_fixture, mock_messaging_service, monkeypatch
):
    """AS-1: order created → order_confirmation message dispatched within
    the handler (the 30s SLA is observability-level; here we just confirm
    the dispatch happens)."""
    with patch(
        "src.infrastructure.events.handlers.whatsapp_notification_handler"
        ".get_whatsapp_service",
        new=AsyncMock(return_value=mock_messaging_service),
    ):
        await handle_order_created_whatsapp(order_created_event_fixture)

    mock_messaging_service.send_order_confirmation.assert_awaited_once()
    call = mock_messaging_service.send_order_confirmation.await_args
    assert call.args[1] == order_created_event_fixture.order_number
    # Total formatted as "{total:.2f} {currency}"
    assert "250.00" in call.args[2]
    assert "EGP" in call.args[2]


# ── AS-2 / FR-002 ────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_order_paid_dispatches_payment_received(
    seeded_store, seeded_customer_optin_active, mock_messaging_service
):
    event = OrderPaidEvent(
        order_id=uuid4(),
        order_number="A-0043",
        store_id=seeded_store.id,
        customer_id=seeded_customer_optin_active.id,
        total=400.00,
    )
    with patch(
        "src.infrastructure.events.handlers.whatsapp_notification_handler"
        ".get_whatsapp_service",
        new=AsyncMock(return_value=mock_messaging_service),
    ):
        await handle_order_paid_whatsapp(event)

    mock_messaging_service.send_payment_received.assert_awaited_once()


# ── AS-3 / opt-out ───────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_order_created_skipped_when_customer_opted_out(
    seeded_store, seeded_customer_opted_out, mock_messaging_service, caplog
):
    """When the customer has an explicit opt-out row, the handler must
    skip with reason=opt_out and emit NO send."""
    event = OrderCreatedEvent(
        order_id=uuid4(),
        order_number="A-0044",
        store_id=seeded_store.id,
        customer_id=seeded_customer_opted_out.id,
        total=100.00,
        currency="EGP",
    )
    with patch(
        "src.infrastructure.events.handlers.whatsapp_notification_handler"
        ".get_whatsapp_service",
        new=AsyncMock(return_value=mock_messaging_service),
    ):
        await handle_order_created_whatsapp(event)

    mock_messaging_service.send_order_confirmation.assert_not_awaited()
    # Structured skip-reason log must be emitted for FR-039 observability
    assert any(
        rec.message == "whatsapp_order_created_skipped"
        and getattr(rec, "reason", None) == "opt_out"
        for rec in caplog.records
    )


# ── AS-4 / merchant toggle off ───────────────────────────────────────


@pytest.mark.asyncio
async def test_order_created_skipped_when_merchant_toggle_off(
    seeded_store_notifications_disabled,
    seeded_customer_optin_active,
    mock_messaging_service,
    caplog,
):
    """When store.settings.whatsapp_notifications.order_confirmation = False
    the handler must skip with reason=merchant_setting_off."""
    event = OrderCreatedEvent(
        order_id=uuid4(),
        order_number="A-0045",
        store_id=seeded_store_notifications_disabled.id,
        customer_id=seeded_customer_optin_active.id,
        total=100.00,
        currency="EGP",
    )
    with patch(
        "src.infrastructure.events.handlers.whatsapp_notification_handler"
        ".get_whatsapp_service",
        new=AsyncMock(return_value=mock_messaging_service),
    ):
        await handle_order_created_whatsapp(event)

    mock_messaging_service.send_order_confirmation.assert_not_awaited()
    assert any(
        rec.message == "whatsapp_order_created_skipped"
        and getattr(rec, "reason", None) == "merchant_setting_off"
        for rec in caplog.records
    )


# ── AS-5 / FR-005 idempotency ────────────────────────────────────────


@pytest.mark.asyncio
async def test_duplicate_order_created_event_dispatches_only_once(
    order_created_event_fixture, mock_messaging_service, seed_message_log_for_event
):
    """Replaying the same event MUST NOT produce a duplicate message —
    the handler queries message_log for an existing send with matching
    metadata.order_id + event_tag=order_created and short-circuits with
    reason=already_sent."""
    # Pre-seed a successful send entry for this order to simulate the
    # first dispatch having already happened.
    await seed_message_log_for_event(
        order_id=order_created_event_fixture.order_id,
        store_id=order_created_event_fixture.store_id,
        template_name="order_confirmation",
        event_tag="order_created",
        status="sent",
    )

    with patch(
        "src.infrastructure.events.handlers.whatsapp_notification_handler"
        ".get_whatsapp_service",
        new=AsyncMock(return_value=mock_messaging_service),
    ):
        await handle_order_created_whatsapp(order_created_event_fixture)

    # No new send — the message_log entry blocks via already_sent reason.
    mock_messaging_service.send_order_confirmation.assert_not_awaited()


# ── Edge: customer with no phone ─────────────────────────────────────


@pytest.mark.asyncio
async def test_order_created_silent_no_op_when_customer_has_no_phone(
    seeded_store, seeded_customer_no_phone, mock_messaging_service
):
    """Customers without a phone number must not crash the handler; the
    handler logs a structured skip and returns. The order pipeline
    upstream is unaffected (FR-001 acceptance edge case)."""
    event = OrderCreatedEvent(
        order_id=uuid4(),
        order_number="A-0046",
        store_id=seeded_store.id,
        customer_id=seeded_customer_no_phone.id,
        total=50.00,
        currency="EGP",
    )
    with patch(
        "src.infrastructure.events.handlers.whatsapp_notification_handler"
        ".get_whatsapp_service",
        new=AsyncMock(return_value=mock_messaging_service),
    ):
        await handle_order_created_whatsapp(event)
    mock_messaging_service.send_order_confirmation.assert_not_awaited()


# ── Fixtures expected at the conftest level ──────────────────────────
# These tests assume the following fixtures exist (or get added) in
# tests/conftest.py or tests/integration/conftest.py:
#
#   - db_session: async SQLAlchemy session with RLS tenant context set
#   - seeded_store: a StoreModel with whatsapp_notifications.order_confirmation = True
#   - seeded_store_notifications_disabled: same store with order_confirmation = False
#   - seeded_customer_optin_active: customer with E.164 phone + active opt-in row
#   - seeded_customer_opted_out: customer with an opt-out row (opted_out_at set)
#   - seeded_customer_no_phone: customer.phone IS NULL
#   - seed_message_log_for_event(order_id, store_id, template_name, event_tag, status):
#       inserts a message_log row matching the handler's idempotency key
#
# These fixtures are out of scope for this batch; they land alongside
# the per-US3 dispatcher test fixtures (where the same shape is needed).
