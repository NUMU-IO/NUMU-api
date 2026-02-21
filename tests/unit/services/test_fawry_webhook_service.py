"""Unit tests for FawryWebhookService.

Covers all four webhook statuses, state-transition guards, security
checks (replay protection, timestamp validation), and edge cases.
"""

import time
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from src.application.services.fawry_webhook_service import (
    FawryWebhookService,
    MAX_WEBHOOK_AGE_SECONDS,
    _CANCELED_VALID_PRIOR,
    _EXPIRED_VALID_PRIOR,
    _PAID_VALID_PRIOR,
)
from src.core.entities.order import OrderStatus, PaymentStatus


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_order_model(**overrides):
    """Build a minimal mock OrderModel."""
    order = MagicMock()
    order.id = overrides.get("id", uuid4())
    order.order_number = overrides.get("order_number", "ORD-000001")
    order.store_id = overrides.get("store_id", uuid4())
    order.tenant_id = overrides.get("tenant_id", uuid4())
    order.customer_id = overrides.get("customer_id", uuid4())
    order.status = overrides.get("status", OrderStatus.PENDING)
    order.payment_status = overrides.get("payment_status", PaymentStatus.PENDING)
    order.payment_method = overrides.get("payment_method", "fawry")
    order.payment_id = overrides.get("payment_id", "MERCHANT-REF-001")
    order.paid_at = None
    order.cancelled_at = None
    order.extra_data = overrides.get("extra_data", {})
    order.total = overrides.get("total", 25000)  # 250.00 EGP
    order.currency = "EGP"
    order.line_items = overrides.get("line_items", [
        {"product_id": str(uuid4()), "quantity": 2, "product_name": "Widget"},
    ])
    # Store relationship for messaging
    store = MagicMock()
    store.name = "Test Store"
    store.contact_phone = "+201234567890"
    store.default_language = "en"
    order.store = store
    return order


def _make_db_session(order=None):
    """Build a mock AsyncSession that returns the given order on execute."""
    session = AsyncMock()
    result = MagicMock()
    result.scalar_one_or_none.return_value = order
    session.execute.return_value = result
    session.flush = AsyncMock()
    session.add = MagicMock()
    return session


def _make_cache(is_duplicate=False):
    """Build a mock RedisCacheService."""
    cache = AsyncMock()
    cache.set_if_absent = AsyncMock(return_value=not is_duplicate)
    return cache


# ===========================================================================
# Security Agent tests
# ===========================================================================


class TestReplayProtection:
    """Security Agent: Redis nonce-based replay protection."""

    @pytest.mark.asyncio
    async def test_first_webhook_is_not_replay(self):
        service = FawryWebhookService(
            db=_make_db_session(), cache=_make_cache(is_duplicate=False)
        )
        assert await service.check_replay("REF-001") is False

    @pytest.mark.asyncio
    async def test_duplicate_webhook_is_replay(self):
        service = FawryWebhookService(
            db=_make_db_session(), cache=_make_cache(is_duplicate=True)
        )
        assert await service.check_replay("REF-001") is True

    @pytest.mark.asyncio
    async def test_no_cache_skips_replay_check(self):
        service = FawryWebhookService(db=_make_db_session(), cache=None)
        assert await service.check_replay("REF-001") is False

    @pytest.mark.asyncio
    async def test_replay_check_uses_correct_key(self):
        cache = _make_cache(is_duplicate=False)
        service = FawryWebhookService(db=_make_db_session(), cache=cache)
        await service.check_replay("REF-XYZ")
        cache.set_if_absent.assert_called_once()
        key = cache.set_if_absent.call_args[0][0]
        assert key == "fawry:nonce:REF-XYZ"


class TestTimestampCheck:
    """Security Agent: webhook timestamp freshness."""

    def test_recent_timestamp_accepted(self):
        service = FawryWebhookService(db=_make_db_session())
        now_ms = int(time.time() * 1000)
        assert service.check_timestamp({"timestamp": now_ms}) is True

    def test_stale_timestamp_rejected(self):
        service = FawryWebhookService(db=_make_db_session())
        old_ms = int((time.time() - MAX_WEBHOOK_AGE_SECONDS - 60) * 1000)
        assert service.check_timestamp({"timestamp": old_ms}) is False

    def test_missing_timestamp_accepted(self):
        service = FawryWebhookService(db=_make_db_session())
        assert service.check_timestamp({}) is True

    def test_invalid_timestamp_accepted(self):
        service = FawryWebhookService(db=_make_db_session())
        assert service.check_timestamp({"timestamp": "not-a-number"}) is True

    def test_orderExpiryDate_is_ignored(self):
        """orderExpiryDate is the payment reference expiry, not dispatch time."""
        service = FawryWebhookService(db=_make_db_session())
        future_ms = int((time.time() + 86400) * 1000)  # 24h in the future
        # Only orderExpiryDate present — should be accepted (no timestamp field)
        assert service.check_timestamp({"orderExpiryDate": future_ms}) is True


# ===========================================================================
# PAID status tests (DB Agent + Payment Agent + Audit Agent)
# ===========================================================================


class TestHandlePaid:
    """PAID: order → confirmed, payment → paid, paid_at set, audit logged."""

    @pytest.mark.asyncio
    async def test_paid_updates_order_and_payment(self):
        order = _make_order_model()
        db = _make_db_session(order)
        service = FawryWebhookService(db=db)

        result = await service.handle_paid(
            merchant_ref="MERCHANT-REF-001",
            reference_number="FAWRY-REF-001",
            payment_amount=250.00,
            payment_method="PAYATFAWRY",
            fawry_fees=5.00,
            raw_data={},
        )

        assert result is not None
        assert result.status == OrderStatus.CONFIRMED
        assert result.payment_status == PaymentStatus.PAID
        assert result.paid_at is not None
        assert result.payment_method == "PAYATFAWRY"
        assert result.extra_data["fawry_reference"] == "FAWRY-REF-001"
        assert result.extra_data["fawry_fees_cents"] == 500

    @pytest.mark.asyncio
    async def test_paid_creates_audit_log(self):
        order = _make_order_model()
        db = _make_db_session(order)
        service = FawryWebhookService(db=db)

        await service.handle_paid(
            merchant_ref="MERCHANT-REF-001",
            reference_number="FAWRY-REF-001",
            payment_amount=250.00,
            payment_method="PAYATFAWRY",
            fawry_fees=5.00,
            raw_data={},
        )

        db.add.assert_called()
        audit_log = db.add.call_args[0][0]
        assert audit_log.event_type == "payment.paid"
        assert audit_log.resource_type == "order"
        assert audit_log.details["fawry_reference"] == "FAWRY-REF-001"

    @pytest.mark.asyncio
    async def test_paid_order_not_found_returns_none(self):
        db = _make_db_session(order=None)
        service = FawryWebhookService(db=db)

        result = await service.handle_paid(
            merchant_ref="UNKNOWN",
            reference_number="FAWRY-REF-999",
            payment_amount=0,
            payment_method=None,
            fawry_fees=0,
            raw_data={},
        )
        assert result is None

    @pytest.mark.asyncio
    async def test_paid_ignored_when_already_confirmed(self):
        """Idempotency: a second PAID webhook for an already-confirmed order is ignored."""
        order = _make_order_model(status=OrderStatus.CONFIRMED)
        db = _make_db_session(order)
        service = FawryWebhookService(db=db)

        result = await service.handle_paid(
            merchant_ref="MERCHANT-REF-001",
            reference_number="FAWRY-REF-001",
            payment_amount=250.00,
            payment_method="PAYATFAWRY",
            fawry_fees=5.00,
            raw_data={},
        )

        # Order returned unchanged, no flush/audit
        assert result is order
        assert result.status == OrderStatus.CONFIRMED
        db.flush.assert_not_called()
        db.add.assert_not_called()

    @pytest.mark.asyncio
    async def test_paid_ignored_when_shipped(self):
        """A late PAID webhook for a shipped order must not regress status."""
        order = _make_order_model(status=OrderStatus.SHIPPED)
        db = _make_db_session(order)
        service = FawryWebhookService(db=db)

        result = await service.handle_paid(
            merchant_ref="MERCHANT-REF-001",
            reference_number="FAWRY-REF-001",
            payment_amount=250.00,
            payment_method="PAYATFAWRY",
            fawry_fees=5.00,
            raw_data={},
        )

        assert result.status == OrderStatus.SHIPPED
        db.flush.assert_not_called()

    @pytest.mark.asyncio
    async def test_paid_accepted_from_payment_failed(self):
        """A retry after PAYMENT_FAILED should be accepted."""
        order = _make_order_model(status=OrderStatus.PAYMENT_FAILED)
        db = _make_db_session(order)
        service = FawryWebhookService(db=db)

        result = await service.handle_paid(
            merchant_ref="MERCHANT-REF-001",
            reference_number="FAWRY-REF-001",
            payment_amount=250.00,
            payment_method="PAYATFAWRY",
            fawry_fees=5.00,
            raw_data={},
        )

        assert result.status == OrderStatus.CONFIRMED
        assert result.payment_status == PaymentStatus.PAID


# ===========================================================================
# EXPIRED status tests (DB Agent + Payment Agent + Inventory Agent + Audit)
# ===========================================================================


class TestHandleExpired:
    """EXPIRED: payment → failed, order → payment_failed, inventory released."""

    @pytest.mark.asyncio
    async def test_expired_marks_payment_failed(self):
        order = _make_order_model()
        db = _make_db_session(order)
        service = FawryWebhookService(db=db)

        result = await service.handle_expired(
            merchant_ref="MERCHANT-REF-001",
            raw_data={},
        )

        assert result is not None
        assert result.payment_status == PaymentStatus.FAILED
        assert result.status == OrderStatus.PAYMENT_FAILED

    @pytest.mark.asyncio
    async def test_expired_releases_inventory(self):
        product_id = str(uuid4())
        order = _make_order_model(
            line_items=[{"product_id": product_id, "quantity": 3, "product_name": "Widget"}]
        )
        db = _make_db_session(order)
        service = FawryWebhookService(db=db)

        await service.handle_expired(
            merchant_ref="MERCHANT-REF-001",
            raw_data={},
        )

        # select (lookup) + flush + execute (inventory update)
        assert db.execute.call_count >= 2

    @pytest.mark.asyncio
    async def test_expired_creates_audit_log(self):
        order = _make_order_model()
        db = _make_db_session(order)
        service = FawryWebhookService(db=db)

        await service.handle_expired(merchant_ref="MERCHANT-REF-001", raw_data={})

        db.add.assert_called()
        audit_log = db.add.call_args[0][0]
        assert audit_log.event_type == "payment.expired"

    @pytest.mark.asyncio
    async def test_expired_order_not_found(self):
        db = _make_db_session(order=None)
        service = FawryWebhookService(db=db)
        result = await service.handle_expired(merchant_ref="UNKNOWN", raw_data={})
        assert result is None

    @pytest.mark.asyncio
    async def test_expired_ignored_when_already_confirmed(self):
        """A late EXPIRED webhook for an already-confirmed order is ignored."""
        order = _make_order_model(status=OrderStatus.CONFIRMED)
        db = _make_db_session(order)
        service = FawryWebhookService(db=db)

        result = await service.handle_expired(merchant_ref="MERCHANT-REF-001", raw_data={})

        assert result.status == OrderStatus.CONFIRMED
        db.flush.assert_not_called()


# ===========================================================================
# CANCELED status tests (DB + Payment + Messaging + Audit Agents)
# ===========================================================================


class TestHandleCanceled:
    """CANCELED: order → cancelled, payment → failed, WhatsApp sent."""

    @pytest.mark.asyncio
    async def test_canceled_updates_order(self):
        order = _make_order_model()
        db = _make_db_session(order)
        service = FawryWebhookService(db=db)

        result = await service.handle_canceled(
            merchant_ref="MERCHANT-REF-001",
            raw_data={},
        )

        assert result is not None
        assert result.status == OrderStatus.CANCELLED
        assert result.payment_status == PaymentStatus.FAILED
        assert result.cancelled_at is not None

    @pytest.mark.asyncio
    async def test_canceled_sends_whatsapp(self):
        order = _make_order_model()
        db = _make_db_session(order)
        messaging = AsyncMock()
        messaging.send_message = AsyncMock(return_value=MagicMock(success=True))
        service = FawryWebhookService(db=db, messaging=messaging)

        await service.handle_canceled(merchant_ref="MERCHANT-REF-001", raw_data={})

        messaging.send_message.assert_called_once()
        content = messaging.send_message.call_args[0][0]
        assert content.type.value == "order_cancelled"

    @pytest.mark.asyncio
    async def test_canceled_no_messaging_still_succeeds(self):
        order = _make_order_model()
        db = _make_db_session(order)
        service = FawryWebhookService(db=db, messaging=None)

        result = await service.handle_canceled(
            merchant_ref="MERCHANT-REF-001", raw_data={}
        )
        assert result is not None
        assert result.status == OrderStatus.CANCELLED

    @pytest.mark.asyncio
    async def test_canceled_messaging_failure_does_not_break(self):
        order = _make_order_model()
        db = _make_db_session(order)
        messaging = AsyncMock()
        messaging.send_message = AsyncMock(side_effect=Exception("WhatsApp down"))
        service = FawryWebhookService(db=db, messaging=messaging)

        result = await service.handle_canceled(
            merchant_ref="MERCHANT-REF-001", raw_data={}
        )
        assert result.status == OrderStatus.CANCELLED

    @pytest.mark.asyncio
    async def test_canceled_creates_audit_log(self):
        order = _make_order_model()
        db = _make_db_session(order)
        service = FawryWebhookService(db=db)

        await service.handle_canceled(merchant_ref="MERCHANT-REF-001", raw_data={})

        db.add.assert_called()
        audit_log = db.add.call_args[0][0]
        assert audit_log.event_type == "payment.canceled"

    @pytest.mark.asyncio
    async def test_canceled_ignored_when_shipped(self):
        """Shipped orders cannot be cancelled via webhook."""
        order = _make_order_model(status=OrderStatus.SHIPPED)
        db = _make_db_session(order)
        service = FawryWebhookService(db=db)

        result = await service.handle_canceled(
            merchant_ref="MERCHANT-REF-001", raw_data={}
        )

        assert result.status == OrderStatus.SHIPPED
        db.flush.assert_not_called()

    @pytest.mark.asyncio
    async def test_canceled_ignored_when_delivered(self):
        """Delivered orders cannot be cancelled via webhook."""
        order = _make_order_model(status=OrderStatus.DELIVERED)
        db = _make_db_session(order)
        service = FawryWebhookService(db=db)

        result = await service.handle_canceled(
            merchant_ref="MERCHANT-REF-001", raw_data={}
        )

        assert result.status == OrderStatus.DELIVERED
        db.flush.assert_not_called()


# ===========================================================================
# REFUNDED status tests (DB + Payment + Audit Agents)
# ===========================================================================


class TestHandleRefunded:
    """REFUNDED: payment → refunded, audit entry with event_type payment.refunded."""

    @pytest.mark.asyncio
    async def test_refunded_marks_payment_refunded(self):
        order = _make_order_model(payment_status=PaymentStatus.PAID)
        db = _make_db_session(order)
        service = FawryWebhookService(db=db)

        result = await service.handle_refunded(
            merchant_ref="MERCHANT-REF-001",
            payment_amount=250.00,
            raw_data={},
        )

        assert result is not None
        assert result.payment_status == PaymentStatus.REFUNDED
        assert result.extra_data["refund_amount"] == 250.00

    @pytest.mark.asyncio
    async def test_refunded_creates_audit_with_correct_event_type(self):
        order = _make_order_model(payment_status=PaymentStatus.PAID)
        db = _make_db_session(order)
        service = FawryWebhookService(db=db)

        await service.handle_refunded(
            merchant_ref="MERCHANT-REF-001",
            payment_amount=250.00,
            raw_data={},
        )

        db.add.assert_called()
        audit_log = db.add.call_args[0][0]
        assert audit_log.event_type == "payment.refunded"
        assert audit_log.details["refund_amount"] == 250.00

    @pytest.mark.asyncio
    async def test_refunded_order_not_found(self):
        db = _make_db_session(order=None)
        service = FawryWebhookService(db=db)
        result = await service.handle_refunded(
            merchant_ref="UNKNOWN", payment_amount=0, raw_data={}
        )
        assert result is None

    @pytest.mark.asyncio
    async def test_refunded_ignored_when_payment_not_paid(self):
        """Cannot refund an order whose payment is still pending."""
        order = _make_order_model(payment_status=PaymentStatus.PENDING)
        db = _make_db_session(order)
        service = FawryWebhookService(db=db)

        result = await service.handle_refunded(
            merchant_ref="MERCHANT-REF-001",
            payment_amount=250.00,
            raw_data={},
        )

        assert result.payment_status == PaymentStatus.PENDING
        db.flush.assert_not_called()

    @pytest.mark.asyncio
    async def test_refunded_ignored_when_already_refunded(self):
        """Idempotency: duplicate REFUNDED webhook is a no-op."""
        order = _make_order_model(payment_status=PaymentStatus.REFUNDED)
        db = _make_db_session(order)
        service = FawryWebhookService(db=db)

        result = await service.handle_refunded(
            merchant_ref="MERCHANT-REF-001",
            payment_amount=250.00,
            raw_data={},
        )

        assert result.payment_status == PaymentStatus.REFUNDED
        db.flush.assert_not_called()
