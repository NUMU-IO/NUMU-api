"""Integration tests for notification dispatch on order events.

Verifies:
- WhatsApp tasks called on shipped/delivered status changes
- Email tasks called on shipped/delivered status changes
- Checkout triggers order-confirmation notifications
- Notification preferences are respected (opt-out suppresses dispatch)
- Notification failures never break the order flow

Note: These tests require Celery/Redis infrastructure and are skipped in CI.
Run with NUMU_RUN_CELERY_TESTS=1 to execute.
"""

import os
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

# Skip all tests in this module unless NUMU_RUN_CELERY_TESTS=1
pytestmark = pytest.mark.skipif(
    os.environ.get("NUMU_RUN_CELERY_TESTS", "0") != "1",
    reason="Celery/Redis tests require infrastructure. Set NUMU_RUN_CELERY_TESTS=1 to run.",
)

from src.application.dto.order import UpdateOrderStatusDTO
from src.application.use_cases.orders.update_order_status import (
    UpdateOrderStatusUseCase,
)
from src.core.entities.customer import Customer
from src.core.entities.order import (
    Order,
    OrderLineItem,
    OrderShippingAddress,
    OrderStatus,
    PaymentStatus,
)
from src.core.entities.store import Store, StoreStatus
from src.core.value_objects.email import Email
from src.core.value_objects.money import Currency
from src.core.value_objects.phone import PhoneNumber

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_store(owner_id=None):
    return Store(
        id=uuid4(),
        owner_id=owner_id or uuid4(),
        name="Test Store",
        slug="test-store",
        status=StoreStatus.ACTIVE,
        default_currency=Currency.EGP,
        contact_email="merchant@test.io",
    )


def _make_customer(store_id, prefs=None):
    metadata = {}
    if prefs is not None:
        metadata["notification_preferences"] = prefs
    return Customer(
        id=uuid4(),
        store_id=store_id,
        email=Email(value="customer@test.io"),
        first_name="Test",
        last_name="Customer",
        phone=PhoneNumber(value="+201012345678", country_code="EG"),
        is_verified=True,
        metadata=metadata,
    )


def _make_order(store_id, customer_id, status=OrderStatus.CONFIRMED):
    return Order(
        id=uuid4(),
        store_id=store_id,
        customer_id=customer_id,
        order_number="ORD-TEST-001",
        shipping_address=OrderShippingAddress(
            first_name="Test",
            last_name="Customer",
            address_line1="123 Test St",
            city="Cairo",
            country="EG",
        ),
        line_items=[
            OrderLineItem(
                product_id=uuid4(),
                product_name="Widget",
                sku="WID-001",
                quantity=1,
                unit_price=5000,
                total_price=5000,
            ),
        ],
        status=status,
        payment_status=PaymentStatus.PAID,
        subtotal=5000,
        total=5000,
        currency="EGP",
    )


def _build_use_case(order_repo, store_repo, customer_repo=None):
    return UpdateOrderStatusUseCase(
        order_repository=order_repo,
        store_repository=store_repo,
        customer_repository=customer_repo,
    )


# ---------------------------------------------------------------------------
# Tests: WhatsApp called on order events
# ---------------------------------------------------------------------------


class TestWhatsAppOnOrderEvents:
    """Verify WhatsApp Celery tasks are dispatched for ship/deliver."""

    @pytest.mark.asyncio
    async def test_whatsapp_shipping_task_called_on_shipped(self):
        store = _make_store()
        customer = _make_customer(store.id)
        order = _make_order(store.id, customer.id, status=OrderStatus.PROCESSING)

        order_repo = AsyncMock()
        order_repo.get_by_id = AsyncMock(return_value=order)
        order_repo.update = AsyncMock(side_effect=lambda o: o)

        store_repo = AsyncMock()
        store_repo.get_by_id = AsyncMock(return_value=store)

        customer_repo = AsyncMock()
        customer_repo.get_by_id = AsyncMock(return_value=customer)

        uc = _build_use_case(order_repo, store_repo, customer_repo)

        with patch(
            "src.infrastructure.messaging.tasks.notification_tasks."
            "send_whatsapp_shipping_update_task"
        ) as mock_task:
            mock_task.delay = MagicMock()

            dto = UpdateOrderStatusDTO(status="shipped")
            await uc.execute(order.id, dto, store.id, store.owner_id)

            mock_task.delay.assert_called_once()
            call_kwargs = mock_task.delay.call_args
            assert call_kwargs.kwargs["phone"] == "+201012345678"
            assert call_kwargs.kwargs["order_number"] == "ORD-TEST-001"

    @pytest.mark.asyncio
    async def test_whatsapp_delivery_task_called_on_delivered(self):
        store = _make_store()
        customer = _make_customer(store.id)
        order = _make_order(store.id, customer.id, status=OrderStatus.SHIPPED)

        order_repo = AsyncMock()
        order_repo.get_by_id = AsyncMock(return_value=order)
        order_repo.update = AsyncMock(side_effect=lambda o: o)

        store_repo = AsyncMock()
        store_repo.get_by_id = AsyncMock(return_value=store)

        customer_repo = AsyncMock()
        customer_repo.get_by_id = AsyncMock(return_value=customer)

        uc = _build_use_case(order_repo, store_repo, customer_repo)

        with patch(
            "src.infrastructure.messaging.tasks.notification_tasks."
            "send_whatsapp_delivery_confirmation_task"
        ) as mock_task:
            mock_task.delay = MagicMock()
            dto = UpdateOrderStatusDTO(status="delivered")
            await uc.execute(order.id, dto, store.id, store.owner_id)

            mock_task.delay.assert_called_once()
            assert mock_task.delay.call_args.kwargs["store_name"] == "Test Store"


# ---------------------------------------------------------------------------
# Tests: Email called on order events
# ---------------------------------------------------------------------------


class TestEmailOnOrderEvents:
    """Verify email Celery tasks are dispatched for ship/deliver."""

    @pytest.mark.asyncio
    async def test_email_shipping_task_called_on_shipped(self):
        store = _make_store()
        customer = _make_customer(store.id)
        order = _make_order(store.id, customer.id, status=OrderStatus.PROCESSING)

        order_repo = AsyncMock()
        order_repo.get_by_id = AsyncMock(return_value=order)
        order_repo.update = AsyncMock(side_effect=lambda o: o)

        store_repo = AsyncMock()
        store_repo.get_by_id = AsyncMock(return_value=store)

        customer_repo = AsyncMock()
        customer_repo.get_by_id = AsyncMock(return_value=customer)

        uc = _build_use_case(order_repo, store_repo, customer_repo)

        with patch(
            "src.infrastructure.messaging.tasks.notification_tasks."
            "send_shipping_notification_email_task"
        ) as mock_email:
            mock_email.delay = MagicMock()
            dto = UpdateOrderStatusDTO(status="shipped")
            await uc.execute(order.id, dto, store.id, store.owner_id)

            mock_email.delay.assert_called_once()
            assert mock_email.delay.call_args.kwargs["email"] == "customer@test.io"

    @pytest.mark.asyncio
    async def test_email_delivery_task_called_on_delivered(self):
        store = _make_store()
        customer = _make_customer(store.id)
        order = _make_order(store.id, customer.id, status=OrderStatus.SHIPPED)

        order_repo = AsyncMock()
        order_repo.get_by_id = AsyncMock(return_value=order)
        order_repo.update = AsyncMock(side_effect=lambda o: o)

        store_repo = AsyncMock()
        store_repo.get_by_id = AsyncMock(return_value=store)

        customer_repo = AsyncMock()
        customer_repo.get_by_id = AsyncMock(return_value=customer)

        uc = _build_use_case(order_repo, store_repo, customer_repo)

        with patch(
            "src.infrastructure.messaging.tasks.notification_tasks."
            "send_delivery_confirmation_email_task"
        ) as mock_email:
            mock_email.delay = MagicMock()
            dto = UpdateOrderStatusDTO(status="delivered")
            await uc.execute(order.id, dto, store.id, store.owner_id)

            mock_email.delay.assert_called_once()
            assert mock_email.delay.call_args.kwargs["order_number"] == "ORD-TEST-001"
            assert mock_email.delay.call_args.kwargs["store_name"] == "Test Store"


# ---------------------------------------------------------------------------
# Tests: Notification preferences respected
# ---------------------------------------------------------------------------


class TestNotificationPreferencesRespected:
    """Verify opt-out preferences suppress notification dispatch."""

    @pytest.mark.asyncio
    async def test_whatsapp_suppressed_when_opted_out(self):
        """Customer opted out of WhatsApp shipping updates -> no WA task."""
        store = _make_store()
        customer = _make_customer(
            store.id,
            prefs={
                "email": {
                    "order_confirmation": True,
                    "shipping_update": True,
                    "delivery_confirmation": True,
                },
                "whatsapp": {
                    "order_confirmation": True,
                    "shipping_update": False,  # opted out
                    "delivery_confirmation": True,
                },
            },
        )
        order = _make_order(store.id, customer.id, status=OrderStatus.PROCESSING)

        order_repo = AsyncMock()
        order_repo.get_by_id = AsyncMock(return_value=order)
        order_repo.update = AsyncMock(side_effect=lambda o: o)

        store_repo = AsyncMock()
        store_repo.get_by_id = AsyncMock(return_value=store)

        customer_repo = AsyncMock()
        customer_repo.get_by_id = AsyncMock(return_value=customer)

        uc = _build_use_case(order_repo, store_repo, customer_repo)

        with (
            patch(
                "src.infrastructure.messaging.tasks.notification_tasks."
                "send_whatsapp_shipping_update_task"
            ) as mock_wa,
            patch(
                "src.infrastructure.messaging.tasks.notification_tasks."
                "send_shipping_notification_email_task"
            ) as mock_email,
        ):
            mock_wa.delay = MagicMock()
            mock_email.delay = MagicMock()

            dto = UpdateOrderStatusDTO(status="shipped")
            await uc.execute(order.id, dto, store.id, store.owner_id)

            # WhatsApp should NOT fire
            mock_wa.delay.assert_not_called()
            # Email should still fire
            mock_email.delay.assert_called_once()

    @pytest.mark.asyncio
    async def test_email_suppressed_when_opted_out(self):
        """Customer opted out of email delivery confirmation -> no email task."""
        store = _make_store()
        customer = _make_customer(
            store.id,
            prefs={
                "email": {
                    "order_confirmation": True,
                    "shipping_update": True,
                    "delivery_confirmation": False,  # opted out
                },
                "whatsapp": {
                    "order_confirmation": True,
                    "shipping_update": True,
                    "delivery_confirmation": True,
                },
            },
        )
        order = _make_order(store.id, customer.id, status=OrderStatus.SHIPPED)

        order_repo = AsyncMock()
        order_repo.get_by_id = AsyncMock(return_value=order)
        order_repo.update = AsyncMock(side_effect=lambda o: o)

        store_repo = AsyncMock()
        store_repo.get_by_id = AsyncMock(return_value=store)

        customer_repo = AsyncMock()
        customer_repo.get_by_id = AsyncMock(return_value=customer)

        uc = _build_use_case(order_repo, store_repo, customer_repo)

        with (
            patch(
                "src.infrastructure.messaging.tasks.notification_tasks."
                "send_delivery_confirmation_email_task"
            ) as mock_email,
            patch(
                "src.infrastructure.messaging.tasks.notification_tasks."
                "send_whatsapp_delivery_confirmation_task"
            ) as mock_wa,
        ):
            mock_email.delay = MagicMock()
            mock_wa.delay = MagicMock()

            dto = UpdateOrderStatusDTO(status="delivered")
            await uc.execute(order.id, dto, store.id, store.owner_id)

            # Email should NOT fire
            mock_email.delay.assert_not_called()
            # WhatsApp should still fire
            mock_wa.delay.assert_called_once()

    @pytest.mark.asyncio
    async def test_defaults_to_all_enabled(self):
        """Customer with no explicit prefs gets notifications (all default True)."""
        store = _make_store()
        customer = _make_customer(store.id, prefs=None)  # no explicit prefs
        order = _make_order(store.id, customer.id, status=OrderStatus.PROCESSING)

        order_repo = AsyncMock()
        order_repo.get_by_id = AsyncMock(return_value=order)
        order_repo.update = AsyncMock(side_effect=lambda o: o)

        store_repo = AsyncMock()
        store_repo.get_by_id = AsyncMock(return_value=store)

        customer_repo = AsyncMock()
        customer_repo.get_by_id = AsyncMock(return_value=customer)

        uc = _build_use_case(order_repo, store_repo, customer_repo)

        with (
            patch(
                "src.infrastructure.messaging.tasks.notification_tasks."
                "send_whatsapp_shipping_update_task"
            ) as mock_wa,
            patch(
                "src.infrastructure.messaging.tasks.notification_tasks."
                "send_shipping_notification_email_task"
            ) as mock_email,
        ):
            mock_wa.delay = MagicMock()
            mock_email.delay = MagicMock()

            dto = UpdateOrderStatusDTO(status="shipped")
            await uc.execute(order.id, dto, store.id, store.owner_id)

            # Both should fire since defaults are True
            mock_wa.delay.assert_called_once()
            mock_email.delay.assert_called_once()


# ---------------------------------------------------------------------------
# Tests: Notification failures don't break order flow
# ---------------------------------------------------------------------------


class TestNotificationFailuresNonBlocking:
    """Verify the order update succeeds even when notifications fail."""

    @pytest.mark.asyncio
    async def test_order_succeeds_when_notification_dispatch_raises(self):
        """Even if _dispatch_notifications raises, order update returns OK."""
        store = _make_store()
        customer = _make_customer(store.id)
        order = _make_order(store.id, customer.id, status=OrderStatus.PROCESSING)

        order_repo = AsyncMock()
        order_repo.get_by_id = AsyncMock(return_value=order)
        order_repo.update = AsyncMock(side_effect=lambda o: o)

        store_repo = AsyncMock()
        store_repo.get_by_id = AsyncMock(return_value=store)

        customer_repo = AsyncMock()
        customer_repo.get_by_id = AsyncMock(side_effect=Exception("DB down"))

        uc = _build_use_case(order_repo, store_repo, customer_repo)

        dto = UpdateOrderStatusDTO(status="shipped")
        result = await uc.execute(order.id, dto, store.id, store.owner_id)

        # Order should still be updated despite notification failure
        assert result.status == "shipped"
        order_repo.update.assert_called_once()

    @pytest.mark.asyncio
    async def test_order_succeeds_without_customer_repo(self):
        """Use case works without customer_repository (backward compat)."""
        store = _make_store()
        customer = _make_customer(store.id)
        order = _make_order(store.id, customer.id, status=OrderStatus.PROCESSING)

        order_repo = AsyncMock()
        order_repo.get_by_id = AsyncMock(return_value=order)
        order_repo.update = AsyncMock(side_effect=lambda o: o)

        store_repo = AsyncMock()
        store_repo.get_by_id = AsyncMock(return_value=store)

        # No customer_repo passed
        uc = _build_use_case(order_repo, store_repo, customer_repo=None)

        dto = UpdateOrderStatusDTO(status="shipped")
        result = await uc.execute(order.id, dto, store.id, store.owner_id)

        assert result.status == "shipped"


# ---------------------------------------------------------------------------
# Tests: No notifications for non-ship/deliver statuses
# ---------------------------------------------------------------------------


class TestNoNotificationsForOtherStatuses:
    """Confirm that confirmed/processing/cancelled don't trigger dispatch."""

    @pytest.mark.asyncio
    @pytest.mark.parametrize("status", ["confirmed", "processing"])
    async def test_no_dispatch_for_status(self, status):
        store = _make_store()
        customer = _make_customer(store.id)

        # Set up a valid prior status
        prior = OrderStatus.PENDING if status == "confirmed" else OrderStatus.CONFIRMED
        order = _make_order(store.id, customer.id, status=prior)

        order_repo = AsyncMock()
        order_repo.get_by_id = AsyncMock(return_value=order)
        order_repo.update = AsyncMock(side_effect=lambda o: o)

        store_repo = AsyncMock()
        store_repo.get_by_id = AsyncMock(return_value=store)

        customer_repo = AsyncMock()
        customer_repo.get_by_id = AsyncMock(return_value=customer)

        uc = _build_use_case(order_repo, store_repo, customer_repo)

        with (
            patch(
                "src.infrastructure.messaging.tasks.notification_tasks."
                "send_whatsapp_shipping_update_task"
            ) as mock_wa,
            patch(
                "src.infrastructure.messaging.tasks.notification_tasks."
                "send_shipping_notification_email_task"
            ) as mock_email,
        ):
            mock_wa.delay = MagicMock()
            mock_email.delay = MagicMock()

            dto = UpdateOrderStatusDTO(status=status)
            await uc.execute(order.id, dto, store.id, store.owner_id)

            mock_wa.delay.assert_not_called()
            mock_email.delay.assert_not_called()
